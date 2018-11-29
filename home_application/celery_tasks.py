# -*- coding: utf-8 -*-
"""
Tencent is pleased to support the open source community by making 蓝鲸智云(BlueKing) available.
Copyright (C) 2017 THL A29 Limited, a Tencent company. All rights reserved.
Licensed under the MIT License (the "License"); you may not use this file except in compliance with the License.
You may obtain a copy of the License at http://opensource.org/licenses/MIT
Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on
an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and limitations under the License.

celery 任务示例

本地启动celery命令: python  manage.py  celery  worker  --settings=settings
周期性任务还需要启动celery调度命令：python  manage.py  celerybeat --settings=settings
"""
import datetime

from celery import task
from celery.schedules import crontab
from celery.task import periodic_task
from models import IpList, Alarm, Recv
from common.log import logger
from openstack import OpenStackCloud
from blueking.component.shortcuts import get_client_by_user
from utils import get_job_instance_id
import os
import subprocess
import base64


@task()
def add_ip():
    openstackcloud = OpenStackCloud()
    openstackcloud.add_server_ip_list()
    hypervisors = ['172.50.18.212', '172.50.18.213']
    for hypervisor in hypervisors:
        if len(IpList.objects.filter(ip=hypervisor)) == 0:
            IpList.objects.create(ip=hypervisor, type='hypervisor', last_alive_time=datetime.datetime.now())
    if len(IpList.objects.filter(ip="172.50.18.211")) == 0:
        IpList.objects.create(ip="172.50.18.211", type='controller', last_alive_time=datetime.datetime.now())

@task()
def execute_check_ip_task():
    ips = IpList.objects.all()
    if os.path.exists('/data/recv/iplist.txt'):
        os.remove('/data/recv/iplist.txt')
    # 将ip列表写入文件
    with open('/data/recv/iplist.txt','a') as f:
        for ip in ips:
            print ip
            f.write(ip.ip + '\n')
    # 执行ping
    p = subprocess.Popen(r'/data/recv/fping.sh', stdout=subprocess.PIPE)
    p.stdout.read()
    # 读取result文件，更新ip的状态,超时不通的ip，执行重启
    result = open('/data/recv/result.txt', 'r')
    content = result.read().split('\n')
    openstackcloud = OpenStackCloud()
    for i in range(len(content) - 1):
        tmp = content[i]
        ip = tmp[:tmp.index('is') - 1]
        if 'unreachable' in tmp:
            host = IpList.objects.filter(ip=ip)
            print host.type
            print host.values('type')
            if host.values('type') == 'vm':
                logger.error(u"虚拟机{}无法ping通".format(ip))
                dead_time_delay = (datetime.datetime.now() - host.values('last_alive_time')).seconds
                logger.error(u"虚拟机{}无法ping通持续时间: {}".format(ip, dead_time_delay))
                reboot_time_delay = (datetime.datetime.now() - host.values('last_reboot_time')).seconds
                logger.error(u"虚拟机{}重启间隔时间: {}".format(ip, reboot_time_delay))
                print
                if dead_time_delay > 120 and reboot_time_delay > 180:
                    openstackcloud.reboot_server(server_ip=ip, reboot_hard=True)
                    IpList.objects.filter(ip=ip).update(last_reboot_time=datetime.datetime.now())
                    logger.error(u"虚拟机{}超时无法ping通，已执行重启" .format(ip))
            elif host.values('type') == 'hypervisor':
                logger.error(u"计算节点{}无法ping通".format(ip))
                dead_time_delay = (datetime.datetime.now() - host.values('last_alive_time')).seconds
                if dead_time_delay > 120:
                    vms = openstackcloud.get_servers_on_hypervisor(ip)
                    openstackcloud.set_service_status(ip, force_down='true')
                    for vm in vms:
                        openstackcloud.evacuate(vm)
                        vm_ip = openstackcloud.get_ip_by_server_id(vm)
                        if vm_ip is not None:
                            IpList.objects.filter(ip=vm_ip).update(last_reboot_time=datetime.datetime.now())
                    logger.error(u"计算节点{}无法ping通，已执行疏散")
        IpList.objects.filter(ip=ip).update(last_alive_time=datetime.datetime.now())
    now = datetime.datetime.now()
    logger.error(u"check_ip周期任务执行完成，当前时间：{}".format(now))


@task()
def execute_check_service(client, bk_biz_id):
    openstackcloud = OpenStackCloud()
    # [{'ip':172.50.18.211, 'service':'agent'}, ....]
    compute_services_down = openstackcloud.get_compute_service_status()
    network_agents_down = openstackcloud.get_network_agents_status()
    cinderv3_services_down = openstackcloud.get_cinderv3_service_status()
    if len(compute_services_down) != 0:
        for item in compute_services_down:
            service = item['service']
            service_ip = item['ip']
            script_content = base64.b64encode(
                "systemctl restart " + service
            )
            result, instance_id = get_job_instance_id(client, bk_biz_id, service_ip, script_content)
            if result:
                logger.error(u"{}上的{}状态为down，重启服务".format(service_ip, service))
    if len(network_agents_down) != 0:
        for item in network_agents_down:
            agent = item['agent']
            agent_ip = item['ip']
            script_content = base64.b64encode(
                "systemctl restart " + agent
            )
            result, instance_id = get_job_instance_id(client, bk_biz_id, agent_ip, script_content)
            if result:
                logger.error(u"{}上的{}状态为down，重启服务".format(agent_ip, agent))
    if len(cinderv3_services_down) != 0:
        for item in cinderv3_services_down:
            service = item['service']
            service_ip = item['ip']
            script_content = base64.b64encode(
                "systemctl restart " + service
            )
            result, instance_id = get_job_instance_id(client, bk_biz_id, service_ip, script_content)
            if result:
                logger.error(u"{}上的{}状态为down，重启服务".format(service_ip, service))


@periodic_task(run_every=crontab(minute='*/1', hour='*', day_of_week="*"))
def check_ip():
    execute_check_ip_task.apply_async()
    now = datetime.datetime.now()
    logger.error(u"开始调用check_ip周期任务，当前时间：{}".format(now))


@periodic_task(run_every=crontab(minute='*/2', hour='*', day_of_week="*"))
def check_compute_service():
    client = get_client_by_user('admin')
    execute_check_service.apply_async(args=[client, 4])
    now = datetime.datetime.now()
    logger.error(u'开始调用check_service周期任务，当前时间：{}'.format(now))


