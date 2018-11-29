#!/usr/bin/env python
# coding: utf-8

import os
import requests
import logging
from ConfigParser import ConfigParser
import socket
from models import IpList
import datetime


DEVNULL = open(os.devnull, 'wb')
logfile = "/data/recv/OpenStack_Operator.log"
debug = True

level = logging.WARNING if not debug else logging.DEBUG
logging.basicConfig(level=level,
                    format='%(asctime)s %(filename)s[line:%(lineno)d] %(levelname)s %(message)s',
                    datefmt='%a, %d %b %Y %H:%M:%S',
                    filename=logfile,
                    filemode='a')

console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger('').addHandler(console)


class OpenStackCloud(object):
    def __init__(self):
        conffile = "/data/recv/openstack.conf"
        headers = {}
        headers["Content-Type"] = "application/json"
        self.cf = ConfigParser()
        self.cf.read(conffile)
        self.conf = self.get_conf()
        self.headers = headers

        self.catalog, self.token = self.get_token()
        self.nova_endpoints_list = [url for url in self.catalog if url["name"] == "nova"][0]["endpoints"]
        self.neutron_endpoints_list = [url for url in self.catalog if url["name"] == "neutron"][0]["endpoints"]
        self.cinderv3_endpoints_list = [url for url in self.catalog if url["name"] == "cinderv3"][0]["endpoints"]
        self.nova_url = \
            [endpoint for endpoint in self.nova_endpoints_list if endpoint["interface"] == "public"][0]["url"]
        self.neutron_url = \
            [endpoint for endpoint in self.neutron_endpoints_list if endpoint["interface"] == "public"][0]["url"]
        self.cinderv3_url = \
            [endpoint for endpoint in self.cinderv3_endpoints_list if endpoint["interface"] == "public"][0]["url"]

    def get_conf(self):
        """
        获取环境配置信息
        :return: 包含配置信息的字典conf
        """
        conf = {}
        try:
            conf = {
                'auth_url': self.cf.get('openstack', 'OS_AUTH_URL'),
                'user_domain_name': self.cf.get('openstack', 'OS_USER_DOMAIN_NAME'),
                'project_domain_name': self.cf.get('openstack', 'OS_PROJECT_DOMAIN_NAME'),
                'project_name': self.cf.get('openstack', 'OS_PROJECT_NAME'),
                'username': self.cf.get('openstack', 'OS_USERNAME'),
                'password': self.cf.get('openstack', 'OS_PASSWORD'),
            }
        except Exception as e:
            logging.critical('Failed to load config file.')
            logging.critical(e)
        return conf

    def get_token(self):
        """
        获取临时token
        :return: 服务目录和token
        """
        if self.conf is None:
            return
        headers = self.headers
        url = self.conf['auth_url'] + '/auth/tokens'
        data = '{ "auth": ' \
               '{ "identity": { "methods": ["password"],"password": ' \
               '{"user": {"domain": {"name": "%s"},"name": "%s", "password": "%s"} } }, ' \
               '"scope": { "project": { "domain": { "name": "%s" }, "name":  "%s" } } }}'
        data = data % (self.conf['user_domain_name'], self.conf['username'], self.conf['password'],
                       self.conf['project_domain_name'], self.conf['project_name'])
        res = {}
        try:
            logging.debug('Try to get token...')
            res = requests.post(url, data=data, headers=headers)
            logging.debug('Request url: %s' % res.url)
        except Exception as e:
            msg = 'Failed to get token. data:%s headers:%s' % (data, headers)
            logging.critical(msg)
            logging.critical(e)

        token = res.headers.get('X-Subject-Token')
        catalog = res.json()['token']['catalog']
        return catalog, token

    def get_resp(self, service, suffix, method, data=None, headers=None, params=None, isjson=True):
        """
        单独封装获取响应的函数
        :param service: 访问服务
        :param suffix: 路径后缀
        :param method: 方法
        :param data: 请求body
        :param headers: 请求头
        :param params: 请求参数
        :param isjson: 是否json格式化
        :return: response
        """
        if service is 'nova':
            url = self.nova_url + suffix
        elif service is 'neutron':
            url = self.neutron_url + suffix
        elif service is 'cinderv3':
            url = self.cinderv3_url + suffix
        else:
            raise Exception('请提供服务类型')
        if headers is None:
            headers = self.headers.copy()
        headers['X-Auth-Token'] = self.token
        headers["X-OpenStack-Nova-API-Version"] = "2.53"
        """获取requests方法"""
        req = getattr(requests, method)
        res = {}
        try:
            res = req(url, data=data, headers=headers, params=params, verify=False)
            logging.debug("Request url: %s" % res.url)
        except Exception as e:
            msg = "Fail to %s %s data: %s headers: %s" % (method, suffix, data, headers)
            logging.critical(msg)
            logging.critical(e)

        """token过期，重新获取"""
        if res.status_code == 401:
            logging.warn("Token expired. Trying to get token again.")
            self.catalog, self.token = self.get_token()
        if isjson:
            res = res.json()

        return res

    def get_compute_service_status(self):
        suffix = '/os-services'
        services = self.get_resp(service='nova', suffix=suffix, method='get')['services']
        services_down = [{'ip': socket.gethostbyname(service['host']), 'service': 'openstack-' + service['binary']}
                         for service in services if service['state'] == 'down' and service['status'] == 'enabled']
        return services_down

    def get_network_agents_status(self):
        suffix = '/v2.0/agents'
        agents = self.get_resp(service='neutron', suffix=suffix, method='get')['agents']
        agents_down = [{'ip': socket.gethostbyname(agent['host']), 'agent': agent['binary']} for agent in agents
                       if agent['alive'] is False]
        return agents_down

    def get_cinderv3_service_status(self):
        suffix = '/os-services'
        services = self.get_resp(service='cinderv3', suffix=suffix, method='get')['services']
        services_down = []
        for service in services:
            host = service['host']
            if service['binary'] == 'cinder-volume':
                host = host[:host.index('@')]
            if service['state'] == 'down' and service['status'] == 'enabled':
                services_down.append({'ip': socket.gethostbyname(host), 'service': 'openstack-' + service['binary']})
        return services_down

    def locate_server_by_ip(self, server_ip):
        """根据server IP定位需要操作的server id，此处忽略了网络重叠，请不要为云主机挂载两个相同的IP！"""
        servers = self.get_resp(service='nova', suffix="/servers/detail", method="get")
        for server in servers["servers"]:
                for network, subnets in server["addresses"].items():
                    try:
                        for subnet in subnets:
                            if subnet["addr"] == server_ip:
                                return server["id"]
                    except Exception as e:
                        msg = "Can't find server by IP:%s" % server_ip
                        logging.critical(msg)
                        logging.critical(e)

    def get_ip_by_server_id(self, server_id):
        suffix = "/servers/%s" % server_id
        server = self.get_resp(service='nova', suffix=suffix, method="get")['server']
        for network, subnets in server["addresses"].items():
                for subnet in subnets:
                    server_ip = subnet["addr"]
                    if IpList.objects.filter(ip=server_ip) is not None:
                        return server_ip
        return None

    def reboot_server(self, server_ip, reboot_hard=True):
        """
        重启云主机
        :param server_ip: 云主机ip
        :param reboot_hard: 是否执行硬重启，默认为True
        :return: 成功为True
        """
        server_id = self.locate_server_by_ip(server_ip)
        suffix = "/servers/%s/action" % server_id
        data = '{"reboot":{"type": "%s"}}' % "HARD" if reboot_hard else "SOFT"
        try:
            res = self.get_resp(service='nova', suffix=suffix, method="post", data=data, isjson=False)
            msg = "Rebooting server:%s" % server_ip
            logging.info(msg)
            return res.ok
        except Exception as e:
            msg = "Reboot server %s fail." % server_id
            logging.critical(msg)
            logging.critical(e)

    def get_hypervisor_info(self, hypervisor_ip):
        """
        获取计算节点信息
        :param hypervisor_ip: 计算节点ip
        :return: 计算节点信息字典
        """
        suffix = "/os-hypervisors/detail"
        hypervisors = self.get_resp(service='nova', suffix=suffix, method="get")["hypervisors"]
        hypervisor_info = {}
        try:
            hypervisor_info["id"] = [hypervisor["id"] for hypervisor in hypervisors
                                     if hypervisor["host_ip"] == hypervisor_ip][0]
            hypervisor_info["hostname"] = [hypervisor["hypervisor_hostname"] for hypervisor in hypervisors
                                           if hypervisor["host_ip"] == hypervisor_ip][0]

        except Exception as e:
            msg = "Can't get info from hypervisor: %s." % hypervisor_ip
            logging.critical(msg)
            logging.critical(e)
        return hypervisor_info

    def get_servers_on_hypervisor(self, hypervisor_ip):
        """
        获取计算节点上运行的云主机
        :param hypervisor_ip: 计算节点ip
        :return: 云主机uuid列表
        """
        hypervisor_id = self.get_hypervisor_info(hypervisor_ip)["id"]
        suffix = "/os-hypervisors/%s?with_servers=True" % hypervisor_id
        try:
            servers = [server["uuid"] for server in self.get_resp(service='nova', suffix=suffix, method="get")["hypervisor"]["servers"]]
            return servers
        except Exception as e:
            msg = "Can't get servers on hypervisor: %s." % hypervisor_ip
            logging.critical(msg)
            logging.critical(e)

    def evacuate(self, server_id):
        """
        疏散云主机
        :param server_id: 云主机uuid
        :return: True
        """
        suffix = "/servers/%s/action" % server_id
        data = '{"evacuate":{}}'
        try:
            msg = "Evacuating server %s." % server_id
            logging.info(msg)
            res = self.get_resp(service='nova', suffix=suffix, method='post', data=data, isjson=False)
            return res.ok
        except Exception as e:
            msg = "Evacuate server %s fail." % server_id
            logging.critical(msg)
            logging.critical(e)

    def set_service_status(self, hypervisor_ip, force_down):
        """
        关闭/开启指定计算节点的nova-compute服务，保证疏散成功
        :param hypervisor_ip: 计算节点ip
        :param force_down: 'true'关闭服务, 'false'开启服务
        :return: True
        """
        hypervisor_hostname = self.get_hypervisor_info(hypervisor_ip=hypervisor_ip)["hostname"]
        services = self.get_resp(service='nova', suffix="/os-services", method="get")["services"]
        service_id = [service["id"] for service in services if service["host"] == hypervisor_hostname][0]
        data = '{"forced_down": %s}' % force_down
        suffix = "/os-services/%s" % service_id
        try:
            if force_down == "true":
                msg = "Forcing service down on hypervisor %s." % hypervisor_ip
                logging.info(msg)
            else:
                msg = "Forcing service up on hypervisor %s." % hypervisor_ip
                logging.info(msg)
            res = self.get_resp(service='nova', suffix=suffix, method='put', data=data, isjson=False)
            return res.ok
        except Exception as e:
            msg = "Fail to set service status on hypervisor %s." % hypervisor_ip
            logging.critical(msg)
            logging.critical(e)

    def add_server_ip_list(self):
        servers = self.get_resp(service='nova', suffix="/servers/detail", params='', method="get")['servers']
        for server in servers:
            ip = server['addresses'].values()[0][0]['addr']
            if len(IpList.objects.filter(ip=ip)) == 0:
                IpList.objects.create(ip=ip, type='vm', auto_reboot=True,ignore_seconds=170)


