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
def execute_add_ip():
    openstackcloud = OpenStackCloud()
    openstackcloud.add_server_ip_list()
    hypervisors = ['172.50.18.212', '172.50.18.213']
    for hypervisor in hypervisors:
        if len(IpList.objects.filter(ip=hypervisor)) == 0:
            IpList.objects.create(ip=hypervisor, type='hypervisor', ignore_seconds=100, auto_reboot=False)
    if len(IpList.objects.filter(ip="172.50.18.211")) == 0:
        IpList.objects.create(ip="172.50.18.211", type='controller', ignore_seconds=100, auto_reboot=False)

@task()
def execute_check_ip_task():
    ips = IpList.objects.all()
    if os.path.exists('/data/recv/iplist.txt'):
        os.remove('/data/recv/iplist.txt')
    # 将ip列表写入文件
    with open('/data/recv/iplist.txt','a') as f:
        for ip in ips:
            f.write(ip.ip + '\n')
    # 执行ping
    p = subprocess.Popen(r'/data/recv/fping.sh', stdout=subprocess.PIPE)
    p.stdout.read()
    # 读取result文件，更新ip的状态,超时不通的ip，执行重启
    result = open('/data/recv/result.txt', 'r')
    content = result.read().split('\n')
    openstackcloud = OpenStackCloud()
    now = datetime.datetime.now()
    for i in range(len(content) - 1):
        tmp = content[i]
        ip = tmp[:tmp.index('is') - 1]
        # 对于ping不可达的主机
        if 'unreachable' in tmp:
            # 过滤相应主机的记录
            host = IpList.objects.filter(ip=ip)[0]
            # 若为虚拟机，且接入自愈，做以下处理
            if host.type == 'vm':
                # 查询告警记录
                alarm_records = Alarm.objects.filter(ip=host.ip)
                # 若存在相应IP的告警记录，判断是否已进行过自愈，已自愈的重新创建告警，未自愈的根据时间判断是否进行重启
                if len(alarm_records) != 0:
                    last_alarm = alarm_records.order_by('-id')[0]
                    # 上次告警已被处理，创建新告警
                    print "上次告警处理结果: {}".format(last_alarm.recv_result)
                    if last_alarm.recv_result == 'healed':
                        Alarm.objects.create(ip=host.ip, type='OpenStack虚拟机', alarm_time=now, alarm_content="ping不可达", alarm_level="important")
                        logger.error(u"虚拟机 {} ping不可达，已创建告警".format(ip))
                        continue
                    # 第一次处理告警
                    if host.last_reboot_time is None:
                        logger.error(u"虚拟机 {} ping不可达时间间隔：{}".format(ip, (now - last_alarm.alarm_time).seconds))
                        if (now - last_alarm.alarm_time).seconds > host.ignore_seconds:
                            res = openstackcloud.reboot_server(server_ip=ip, reboot_hard=True)
                            if res:
                                logger.error(u"虚拟机 {} 已重启".format(ip))
                            host.last_reboot_time = now
                            host.save()
                            last_alarm.recv_time = now
                            last_alarm.recv_result = 'healed'
                            last_alarm.save()
                            continue
                        continue
                    # 第N次处理告警
                    if (now - last_alarm.alarm_time).seconds > host.ignore_seconds and (now - host.last_reboot_time).seconds > 170:
                        res = openstackcloud.reboot_server(server_ip=ip, reboot_hard=True)
                        if res:
                            logger.error(u"虚拟机 {} 已重启".format(ip))
                        host.last_reboot_time = now
                        host.save()
                        last_alarm.recv_time = now
                        last_alarm.recv_result = 'healed'
                        last_alarm.save()
                # 无告警记录，创建记录
                else:
                    Alarm.objects.create(ip=host.ip, type='OpenStack虚拟机', alarm_time=now, alarm_content="ping不可达",alarm_level="important")
                    logger.error(u"虚拟机 {} ping不可达，已创建告警".format(ip))
            # 若为计算节点，做以下处理
            elif host.type == 'hypervisor':
                logger.error(u"计算节点{}无法ping通".format(ip))
                alarm_records = Alarm.objects.filter(ip=host.ip)
                if len(alarm_records) != 0:
                    last_alarm = alarm_records.order_by('-id')[0]
                    if last_alarm.recv_result == 'healed':
                        Alarm.objects.create(ip=host.ip, type='OpenStack计算节点', alarm_time=now, alarm_content="ping不可达", alarm_level="important")
                    elif (now - last_alarm.alarm_time).seconds > host.ignore_seconds:
                        vms = openstackcloud.get_servers_on_hypervisor(ip)
                        openstackcloud.set_service_status(ip, force_down='true')
                        for vm in vms:
                            openstackcloud.evacuate(vm)
                            vm_ip = openstackcloud.get_ip_by_server_id(vm)
                            if vm_ip is not None:
                                logger.error(u"虚拟机{}已被疏散，重置重启时间间隔".format(vm_ip))
                                IpList.objects.filter(ip=vm_ip)[0].update(last_reboot_time=now)
                            logger.error(u"计算节点{}无法ping通，已执行疏散".format(ip))
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
            if service is 'openstack-nova-compute':
                pass
            else:
                script_content = base64.b64encode(
                    "systemctl restart " + service
                )
                result, instance_id = get_job_instance_id(client, bk_biz_id, service_ip, script_content)
                if result:
                    logger.error(u"{}上的{}状态为down，重启服务".format(service_ip, service))
                    Alarm.objects.create()
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


@periodic_task(run_every=crontab(minute='*/1', hour='*', day_of_week="*"))
def check_compute_service():
    client = get_client_by_user('admin')
    execute_check_service.apply_async(args=[client, 4])
    now = datetime.datetime.now()
    logger.error(u'开始调用check_service周期任务，当前时间：{}'.format(now))


@periodic_task(run_every=crontab(minute='*/10', hour='*', day_of_week="*"))
def add_ip():
    execute_add_ip.apply_async()
    now = datetime.datetime.now()
    logger.error(u'开始调用add_ip周期任务，当前时间：{}'.format(now))
