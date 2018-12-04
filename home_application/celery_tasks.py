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
import datetime, time

from celery import task
from celery.schedules import crontab
from celery.task import periodic_task
from models import IpList, Alarm, Operations
from common.log import logger
from openstack import OpenStackCloud
from blueking.component.shortcuts import get_client_by_user
from utils import get_job_instance_id, get_job_log_content
import os
import subprocess
import base64
import re



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
        # 对于ping不可达的主机，做以下处理
        if 'unreachable' in tmp:
            # 过滤相应主机的记录
            host = IpList.objects.filter(ip=ip)[0]
            if host.type == 'vm':
                if host.ignore_seconds == 0:
                    if host.last_reboot_time is None:
                        Alarm.objects.create(ip=host.ip, type='OpenStack虚拟机', alarm_time=now,
                                             alarm_content="ping不可达", alarm_level="ERROR")
                        res = openstackcloud.reboot_server(server_ip=ip, reboot_hard=True)
                        if res:
                            last_alarm = Alarm.objects.filter(ip=host.ip).last()
                            last_alarm.recv_time = datetime.datetime.now()
                            last_alarm.recv_result = "重启成功"
                            last_alarm.save()
                            host.last_reboot_time = datetime.datetime.now()
                            host.save()
                    elif (now - host.last_reboot_time).seconds > 180:
                        Alarm.objects.create(ip=host.ip, type='OpenStack虚拟机', alarm_time=now,
                                             alarm_content="ping不可达", alarm_level="ERROR")
                        res = openstackcloud.reboot_server(server_ip=ip, reboot_hard=True)
                        if res:
                            last_alarm = Alarm.objects.filter(ip=host.ip).last()
                            last_alarm.recv_time = datetime.datetime.now()
                            last_alarm.recv_result = "重启成功"
                            last_alarm.save()
                            host.last_reboot_time = datetime.datetime.now()
                            host.save()
                    else:
                        logger.error(u"虚拟机 {} 重启期间ping不可达，收敛".format(ip))
                else:
                    # 查询告警记录
                    alarm_records = Alarm.objects.filter(ip=host.ip)
                    # 若存在相应IP的告警记录，判断是否已进行过处理，已处理的重新创建告警，未处理的根据时间判断是否进行处理
                    if len(alarm_records) != 0:
                        last_alarm = alarm_records.order_by('-id')[0]
                        # 第一次处理告警
                        if host.last_reboot_time is None:
                            if (now - last_alarm.alarm_time).seconds > 600:
                                Alarm.objects.create(ip=host.ip, type='OpenStack虚拟机', alarm_time=now,
                                                     alarm_content="ping不可达", alarm_level="ERROR")
                            elif (now - last_alarm.alarm_time).seconds > host.ignore_seconds:
                                res = openstackcloud.reboot_server(server_ip=ip, reboot_hard=True)
                                if res:
                                    logger.error(u"虚拟机 {} 已重启".format(ip))
                                host.last_reboot_time = now
                                host.save()
                                last_alarm.recv_time = now
                                last_alarm.recv_result = '重启成功'
                                last_alarm.save()
                            else:
                                logger.error(u"虚拟机 {} ping不可达时间间隔：{}".format(ip, (now - last_alarm.alarm_time).seconds))
                        # 上一个告警已处理，创建告警
                        elif last_alarm.recv_result == u'重启成功':
                            if (now - host.last_reboot_time).seconds < 180:
                                logger.error(u"虚拟机 {} 重启期间ping不可达，收敛".format(ip))
                            else:
                                Alarm.objects.create(ip=host.ip, type='OpenStack虚拟机', alarm_time=now,
                                                     alarm_content="ping不可达", alarm_level="ERROR")
                                logger.error(u"虚拟机 {} ping不可达，已创建告警".format(ip))
                        # 上一个告警未处理且超过处理时限，创建新告警
                        elif (now - last_alarm.alarm_time).seconds > 600:
                            Alarm.objects.create(ip=host.ip, type='OpenStack虚拟机', alarm_time=now,
                                                 alarm_content="ping不可达", alarm_level="ERROR")
                        # 告警未处理且未超时，若告警超过容忍时间，执行重启
                        elif (now - last_alarm.alarm_time).seconds > host.ignore_seconds and (now - host.last_reboot_time).seconds > 170:
                            logger.error(u"虚拟机 {} ping不可达超过容忍时间，执行重启".format(ip))
                            res = openstackcloud.reboot_server(server_ip=ip, reboot_hard=True)
                            if res:
                                logger.error(u"虚拟机 {} 已重启".format(ip))
                            host.last_reboot_time = now
                            host.save()
                            last_alarm.recv_time = now
                            last_alarm.recv_result = "重启成功"
                            last_alarm.save()
                        # 告警未处理且未超时，若告警未超过容忍时间，执行重启
                        else:
                            logger.error(u"虚拟机 {} ping不可达未超过容忍时间，收敛".format(ip))
                    # 无告警记录，创建记录
                    else:
                        Alarm.objects.create(ip=host.ip, type='OpenStack虚拟机', alarm_time=now, alarm_content="ping不可达",
                                             alarm_level="ERROR")
                        logger.error(u"虚拟机 {} ping不可达，已创建告警".format(ip))
            # 若为计算节点，做以下处理
            elif host.type == 'hypervisor':
                logger.error(u"计算节点 {} ping不可达".format(ip))
                alarm_records = Alarm.objects.filter(ip=host.ip)
                if len(alarm_records) != 0:
                    last_alarm = alarm_records.order_by('-id')[0]
                    # 上次告警已处理，重新创建告警
                    if last_alarm.recv_result == u'疏散成功':
                        Alarm.objects.create(ip=host.ip, type='OpenStack计算节点', alarm_time=now, alarm_content="ping不可达",
                                             alarm_level="ERROR")
                    # 上次告警未处理且超时，重新创建告警
                    elif (now - last_alarm.alarm_time).seconds > 600:
                        Alarm.objects.create(ip=host.ip, type='OpenStack计算节点', alarm_time=now, alarm_content="ping不可达",
                                             alarm_level="ERROR")
                    # 上次告警未处理，超过容忍时间，执行疏散
                    elif (now - last_alarm.alarm_time).seconds > host.ignore_seconds:
                        vms = openstackcloud.get_servers_on_hypervisor(ip)
                        openstackcloud.set_service_status(ip, force_down='true')
                        logger.error(u"计算节点 {} ping不可达，开始执行疏散".format(ip))
                        for vm in vms:
                            openstackcloud.evacuate(vm)
                            vm_ip = openstackcloud.get_ip_by_server_id(vm)
                            if vm_ip is not None:
                                logger.error(u"虚拟机 {} 已被疏散".format(vm_ip))
                                vm_host = IpList.objects.filter(ip=vm_ip)[0]
                                vm_host.last_reboot_time = now
                                vm_host.save()
                        last_alarm.recv_result = "疏散成功"
                        last_alarm.recv_time = now
                        last_alarm.save()
                    else:
                        logger.error(u"计算节点 {} ping不可达，开始执行疏散".format(ip))
                else:
                    Alarm.objects.create(ip=host.ip, type='OpenStack计算节点', alarm_time=now, alarm_content="ping不可达", alarm_level="ERROR")
            elif host.type == 'controller':
                Alarm.objects.create(ip=host.ip, type='OpenStack控制节点', alarm_time=now, alarm_content="ping不可达", alarm_level="ERROR")
    logger.error(u"check_ip周期任务执行完成，当前时间：{}".format(now))


@task()
def execute_check_service(client, bk_biz_id):
    openstackcloud = OpenStackCloud()
    controller = IpList.objects.filter(type='controller')[0]
    script_content = base64.b64encode("""
    #!/bin/bash
    systemctl is-active openstack-nova-api
    systemctl is-active neutron-server
    systemctl is-active openstack-cinder-api
    """)
    result, instance_id = get_job_instance_id(client, bk_biz_id, controller.ip, script_content)
    if result:
        while True:
            is_finished, log_content = get_job_log_content(client, bk_biz_id, instance_id)
            if is_finished:
                break
            time.sleep(1)
        logs = log_content.strip("\n").split("\n")
        if logs[0].encode() == 'active':
            compute_services_down = openstackcloud.get_compute_service_status()
            if len(compute_services_down) != 0:
                for item in compute_services_down:
                    service = item['service']
                    service_ip = item['ip']
                    if service == 'openstack-nova-compute':
                        if not len([i for i, _ in enumerate(compute_services_down) if
                                    _['service'] == 'openstack-nova-conductor']):
                            Alarm.objects.create(ip=service_ip, type="OpenStack服务", alarm_time=datetime.datetime.now(),
                                                 alarm_content="{}不可用".format(service), alarm_level="ERROR")
                            vms = openstackcloud.get_servers_on_hypervisor(service_ip)
                            logger.error(u"计算节点 {} 服务状态为down，开始执行疏散".format(service_ip))
                            for vm in vms:
                                openstackcloud.evacuate(vm)
                                vm_ip = openstackcloud.get_ip_by_server_id(vm)
                                if vm_ip is not None:
                                    logger.error(u"虚拟机 {} 已被疏散".format(vm_ip))
                                    vm_host = IpList.objects.filter(ip=vm_ip)[0]
                                    vm_host.last_reboot_time = datetime.datetime.now()
                                    vm_host.save()
                            last_alarm = Alarm.objects.filter(ip=service_ip,
                                                              alarm_content="{}不可用".format(service)).last()
                            last_alarm.recv_result = "疏散成功"
                            last_alarm.recv_time = datetime.datetime.now()
                            last_alarm.save()
                            script_content = base64.b64encode(
                                "systemctl restart " + service
                            )
                            result, instance_id = get_job_instance_id(client, bk_biz_id, service_ip, script_content)
                            if result:
                                logger.error(u"计算节点 {} 服务状态为down，已重启服务".format(service_ip))
                            else:
                                logger.error(u"计算节点 {} 服务状态为down，重启服务失败".format(service_ip))
                        else:
                            logger.error(u"nova-conductor服务状态为down，计算服务不可用".format(service_ip))
                    else:
                        Alarm.objects.create(ip=service_ip, type="OpenStack服务",
                                             alarm_time=datetime.datetime.now(), alarm_content="{}不可用".format(service), alarm_level="ERROR")
                        script_content = base64.b64encode(
                            "systemctl restart " + service
                        )
                        result, instance_id = get_job_instance_id(client, bk_biz_id, service_ip, script_content)
                        if result:
                            logger.error(u"{}上的{}状态为down，重启服务".format(service_ip, service))
                            check_service_recv.apply_async(args=[service_ip, service, "compute"], countdown=30)
                        else:
                            last_alarm = Alarm.objects.filter(ip=service_ip,
                                                              alarm_content="{}不可用".format(service)).last()
                            last_alarm.recv_result = "自愈失败"
                            last_alarm.recv_time = datetime.datetime.now()
                            last_alarm.save()
        else:
            Alarm.objects.create(ip=controller.ip, type="OpenStack控制节点", alarm_time=datetime.datetime.now(),
                                 alarm_content="nova-api服务不在线", alarm_level="ERROR")
            script_content = base64.b64encode("systemctl restart openstack-nova-api")
            result, instance_id = get_job_instance_id(client, bk_biz_id, controller.ip, script_content)
            if result:
                check_api_status.apply_async(args=[controller.ip, "nova"], countdown=30)
            else:
                last_alarm = Alarm.objects.filter(ip=controller.ip, alarm_content__contains="nova-api服务不在线").last()
                last_alarm.recv_result = "自愈失败"
                last_alarm.recv_time = datetime.datetime.now()
                last_alarm.save()
        if logs[1].encode() == 'active':
            network_agents_down = openstackcloud.get_network_agents_status()
            if len(network_agents_down) != 0:
                for item in network_agents_down:
                    agent = item['agent']
                    agent_ip = item['ip']
                    Alarm.objects.create(ip=agent_ip, type="OpenStack服务", alarm_time=datetime.datetime.now(),
                                         alarm_content="{}不可用".format(agent), alarm_level="ERROR")
                    logger.error(u"{}上的{}状态为down，准备重启代理".format(agent_ip, agent))
                    script_content = base64.b64encode(
                        "systemctl restart " + agent
                    )
                    result, instance_id = get_job_instance_id(client, bk_biz_id, agent_ip, script_content)
                    if result:
                        logger.error(u"{}上的{}状态为down，重启服务代理".format(agent_ip, agent))
                        check_service_recv.apply_async(args=[agent_ip, agent, "network"], countdown=30)
                    else:
                        last_alarm = Alarm.objects.filter(ip=agent_ip, alarm_content="{}不可用".format(agent)).last()
                        last_alarm.recv_result = "自愈失败"
                        last_alarm.recv_time = datetime.datetime.now()
                        last_alarm.save()
        else:
            Alarm.objects.create(ip=controller.ip, type="OpenStack控制节点", alarm_time=datetime.datetime.now(),
                                 alarm_content="neutron-api服务不在线", alarm_level="ERROR")
            script_content = base64.b64encode("systemctl restart neutron-server")
            result, instance_id = get_job_instance_id(client, bk_biz_id, controller.ip, script_content)
            if result:
                check_api_status.apply_async(args=[controller.ip, "neutron"], countdown=30)
            else:
                last_alarm = Alarm.objects.filter(ip=controller.ip, alarm_content__contains="neutron-api服务不在线").last()
                last_alarm.recv_result = "自愈失败"
                last_alarm.recv_time = datetime.datetime.now()
                last_alarm.save()
        if logs[2].encode() == 'active':
            cinderv3_services_down = openstackcloud.get_cinderv3_service_status()
            if len(cinderv3_services_down) != 0:
                for item in cinderv3_services_down:
                    service = item['service']
                    service_ip = item['ip']
                    Alarm.objects.create(ip=service_ip, type="OpenStack服务", alarm_time=datetime.datetime.now(),
                                         alarm_content="{}不可用".format(service), alarm_level="ERROR")
                    logger.error(u"{}上的{}状态为down，准备重启服务".format(service_ip, service))
                    script_content = base64.b64encode(
                        "systemctl restart " + service
                    )
                    result, instance_id = get_job_instance_id(client, bk_biz_id, service_ip, script_content)
                    if result:
                        logger.error(u"{}上的{}状态为down，重启服务成功".format(service_ip, service))
                        check_service_recv.apply_async(args=[service_ip, service, "cinder"], countdown=30)
                    else:
                        last_alarm = Alarm.objects.filter(ip=service_ip, alarm_content="{}不可用".format(service)).last()
                        last_alarm.recv_result = "自愈失败"
                        last_alarm.recv_time = datetime.datetime.now()
                        last_alarm.save()
        else:
            Alarm.objects.create(ip=controller.ip, type="OpenStack控制节点", alarm_time=datetime.datetime.now(),
                                 alarm_content="cinderv3-api服务不在线", alarm_level="ERROR")
            script_content = base64.b64encode("systemctl restart openstack-cinder-api")
            result, instance_id = get_job_instance_id(client, bk_biz_id, controller.ip, script_content)
            if result:
                check_api_status.apply_async(args=[controller.ip, "cinderv3"], countdown=30)
            else:
                last_alarm = Alarm.objects.filter(ip=controller.ip, alarm_content__contains="cinderv3-api服务不在线").last()
                last_alarm.recv_result = "自愈失败"
                last_alarm.recv_time = datetime.datetime.now()
                last_alarm.save()
    else:
        Alarm.objects.create(ip=controller.ip, type="OpenStack控制节点", alarm_time=datetime.datetime.now(),
                             alarm_content="检查服务失败", alarm_level="ERROR")


@task()
def check_service_recv(ip, service, service_type):
    last_alarm = Alarm.objects.filter(ip=ip, alarm_content__contains="{}不可用".format(service)).last()
    openstackcloud = OpenStackCloud()
    if service_type == "compute":
        compute_services_down = openstackcloud.get_compute_service_status()
        if len([i for i, _ in enumerate(compute_services_down) if _['service'] == service and _['ip'] == ip]):
            last_alarm.recv_result = "自愈失败"
            logger.error(u"{}上的{}服务自愈失败".format(ip, service))
        else:
            last_alarm.recv_result = "自愈成功"
            logger.error(u"{}上的{}服务自愈成功".format(ip, service))
        last_alarm.recv_time = datetime.datetime.now()
        last_alarm.save()
    elif service_type == "network":
        network_agents_down = openstackcloud.get_network_agents_status()
        if len([i for i, _ in enumerate(network_agents_down) if _['agent'] == service and _['ip'] == ip]):
            last_alarm.recv_result = "自愈失败"
            logger.error(u"{}上的{}服务自愈失败".format(ip, service))
        else:
            last_alarm.recv_result = "自愈成功"
            logger.error(u"{}上的{}服务自愈成功".format(ip, service))
        last_alarm.recv_time = datetime.datetime.now()
        last_alarm.save()
    else:
        cinderv3_services_down = openstackcloud.get_cinderv3_service_status()
        if len([i for i, _ in enumerate(cinderv3_services_down) if _['service'] == service and _['ip'] == ip]):
            last_alarm.recv_result = "自愈失败"
            logger.error(u"{}上的{}服务自愈失败".format(ip, service))
        else:
            last_alarm.recv_result = "自愈成功"
            logger.error(u"{}上的{}服务自愈成功".format(ip, service))
        last_alarm.recv_time = datetime.datetime.now()
        last_alarm.save()


@task()
def check_api_status(ip, service):
    openstackcloud = OpenStackCloud()
    logger.error(u"正在检查{}上的{}服务".format(ip, service))
    result = openstackcloud.test_service_api_status(service)
    last_alarm = Alarm.objects.filter(ip=ip, alarm_content__contains='{}-api服务不在线'.format(service)).last()
    if result:
        last_alarm.recv_result = "自愈成功"
    else:
        last_alarm.recv_result = "自愈失败"
    last_alarm.recv_time = datetime.datetime.now()
    last_alarm.save()


@periodic_task(run_every=crontab(minute='*/1', hour='*', day_of_week="*"))
def check_ip():
    execute_check_ip_task.apply_async()
    now = datetime.datetime.now()
    Operations.objects.create(celery_opra_time=now, celery_opra_content='检查ping状态')
    logger.error(u"开始调用check_ip周期任务，当前时间：{}".format(now))


@periodic_task(run_every=crontab(minute='*/1', hour='*', day_of_week="*"))
def check_service():
    client = get_client_by_user('admin')
    execute_check_service.apply_async(args=[client, 4])
    now = datetime.datetime.now()
    Operations.objects.create(celery_opra_time=now, celery_opra_content='检查服务状态')
    logger.error(u'开始调用check_service周期任务，当前时间：{}'.format(now))


@periodic_task(run_every=crontab(minute='*/1', hour='*', day_of_week="*"))
def add_ip():
    execute_add_ip.apply_async()
    now = datetime.datetime.now()
    logger.error(u'开始调用add_ip周期任务，当前时间：{}'.format(now))

# -----------------------------------以下为Ceph系统部分----------------------------------------------- #


@periodic_task(run_every=crontab(minute='*/2', hour='*', day_of_week="*"))
def get_osd_state():
    # 写 celery 操作记录
    celery_ip = "172.50.18.214"
    celery_opra_time = datetime.datetime.now()
    celery_opra_content = "检查 OSD 状态"
    Operations.objects.create(ip = celery_ip,celery_opra_time = celery_opra_time, celery_opra_content = celery_opra_content)

    # 检测 osd 状态
    user = "admin"
    client = get_client_by_user(user)
    # 检测 OSD 状态的脚本ID 为 18
    job_kwargs = {
        "bk_biz_id": 3,
        "script_id": 18,
        "script_timeout": 1000,
        "account": "root",
        "is_param_sensitive": 0,
        "script_type": 1,
        "ip_list": [
            {
                "bk_cloud_id": 0,
                "ip": "172.50.18.214"
            }
        ],
    }
    fast_job_excute_resutl = client.job.fast_execute_script(job_kwargs)

    job_instance_id = fast_job_excute_resutl["data"]["job_instance_id"]
    time.sleep(5)
    log_kwargs = {
        "bk_biz_id": 3,  # http://job.blueking.com/?scriptList&appId=3
        "job_instance_id": job_instance_id,  # 脚本管理 里 有个 3
    }

    get_job_instance_log_result = client.job.get_job_instance_log(log_kwargs)
    print get_job_instance_log_result
    # 处理脚本执行结果
    log_content = get_job_instance_log_result["data"][0]["step_results"][0]["ip_logs"][0]["log_content"]
    print log_content
    if "Down" in log_content:
        # set IP and set host
        if "osd.1" in log_content or "osd.4" in log_content or "osd.5" in log_content :
            ip = "172.50.18.214"
            type = "CEPH-1"
        elif "osd.0" in log_content or "osd.2" in log_content or "osd.8" in log_content :
            ip = "172.50.18.215"
            type = "CEPH-2"
        elif "osd.3" in log_content or "osd.6" in log_content or "osd.7" in log_content :
            ip = "172.50.18.216"
            type = "CEPH-3"

        alarm_time = datetime.datetime.now()
        alarm_content = log_content
        alarm_level = "ERROR"

        now = datetime.datetime.now()
        logger.info(u"当前时间：{}, celery get_osd_state 周期任务调用成功!".format(now))

        # 调用 recov_osd 开始进行 OSD 自愈
        recov_osd()
        recv_time = datetime.datetime.now()
        recv_result = "成功"

        # Alarm.objects.create(ip = ip,type = type, alarm_time = alarm_time, alarm_content = alarm_content, \
                # alarm_level = alarm_level, recv_time = recv_time,recv_result = recv_result)


def recov_osd():
    # 写 celery 操作记录
    celery_ip = "172.50.18.214"
    celery_opra_time = datetime.datetime.now()
    celery_opra_content = "启动 OSD 自愈"
    Operations.objects.create(ip=celery_ip, celery_opra_time=celery_opra_time, celery_opra_content=celery_opra_content)

    # 开始 OSD 自愈操作， OSD 自愈的脚本id 为 17
    # 经过测试 OSD 自愈脚本运行时间 需要 35 秒
    user = "admin"
    client = get_client_by_user(user)

    job_recv_osd_kwargs = {
        "bk_biz_id": 3,
        "script_id": 17,
        "script_timeout": 1000,
        "account": "root",
        "is_param_sensitive": 0,
        "script_type": 1,
        "ip_list": [
            {
                "bk_cloud_id": 0,
                "ip": "172.50.18.214"
            }
        ],
    }
    print u"开始执行 OSD 自愈脚本......"
    job_recv_osd_resutl = client.job.fast_execute_script(job_recv_osd_kwargs)
    time.sleep(30)
    now = datetime.datetime.now()
    logger.info(u"当前时间：{}, celery recovory_osd 任务调用成功!".format(now))

    # 写 celery 操作记录
    celery_ip = "172.50.18.214"
    celery_opra_time = datetime.datetime.now()
    celery_opra_content = "OSD 自愈成功"
    Operations.objects.create(ip = celery_ip,celery_opra_time = celery_opra_time, celery_opra_content = celery_opra_content)


@periodic_task(run_every=crontab(minute='*/2', hour='*', day_of_week="*"))
def get_osd_usage():
    # 写 celery 操作记录
    celery_ip = "172.50.18.214"
    celery_opra_time = datetime.datetime.now()
    celery_opra_content = "检测 OSD 使用率"
    Operations.objects.create(ip=celery_ip, celery_opra_time=celery_opra_time, celery_opra_content=celery_opra_content)

    # 检测 osd 状态
    user = "admin"
    client = get_client_by_user(user)
    #检测 OSD 使用率的脚本为 19
    job_kwargs = {
        "bk_biz_id": 3,
        "script_id": 19,
        "script_timeout": 1000,
        "account": "root",
        "is_param_sensitive": 0,
        "script_type": 1,
        "ip_list": [
            {
                "bk_cloud_id": 0,
                "ip": "172.50.18.214"
            }
        ],
    }
    fast_job_excute_resutl = client.job.fast_execute_script(job_kwargs)

    job_instance_id = fast_job_excute_resutl["data"]["job_instance_id"]
    time.sleep(5)
    log_kwargs = {
        "bk_biz_id": 3,  # http://job.blueking.com/?scriptList&appId=3
        "job_instance_id": job_instance_id,  # 脚本管理 里 有个 3
    }

    get_job_instance_log_result = client.job.get_job_instance_log(log_kwargs)
    print get_job_instance_log_result
    # 处理脚本执行结果
    log_content = get_job_instance_log_result["data"][0]["step_results"][0]["ip_logs"][0]["log_content"]
    print log_content  # OSD USED:20.09
    if "USED" in log_content:
        now = datetime.datetime.now()
        logger.info(u"当前时间：{}, celery get_osd_state 周期任务调用成功!".format(now))
        # 判断 OSD 利用率是否超过阀值，若超过进行扩容操作。
        re_usage = r'^OSD USED:(.*?)$'
        usage_result = re.findall(re_usage, log_content)
        if len(usage_result) > 0 :
            ip = "172.50.18.214"
            type = "CEPH-1"
            alarm_time = datetime.datetime.now()
            alarm_content = log_content
            alarm_level = "INFO"
            Alarm.objects.create(ip = ip,type = type, alarm_time = alarm_time, alarm_content = alarm_content, alarm_level = alarm_level)

            usage_level = 90
            usage = usage_result[0] #filter(str.isdigit,usage_result[0].encode("utf-8"))
            print usage
            if float(usage) > usage_level:
                new_osd()
        else:
            ip = "172.50.18.214"
            type = "CEPH-1"
            alarm_time = datetime.datetime.now()
            alarm_content = log_content
            alarm_level = "WRM"
            Alarm.objects.create(ip = ip,type = type, alarm_time = alarm_time, alarm_content = alarm_content, alarm_level = alarm_level)


def new_osd():
    # 写 celery 操作记录
    celery_ip = "172.50.18.214"
    celery_opra_time = datetime.datetime.now()
    celery_opra_content = "启动 OSD 扩容"
    Operations.objects.create(ip=celery_ip, celery_opra_time=celery_opra_time, celery_opra_content=celery_opra_content)

    # 开始 OSD 扩容操作
    user = "admin"
    client = get_client_by_user(user)
    # OSD扩容的脚本id 为 22
    job_recv_osd_kwargs = {
        "bk_biz_id": 3,
        "script_id": 22,
        "script_timeout": 1000,
        "account": "root",
        "is_param_sensitive": 0,
        "script_type": 1,
        "ip_list": [
            {
                "bk_cloud_id": 0,
                "ip": "172.50.18.214"
            }
        ],
    }
    print u"开始执行 OSD 扩容脚本......"
    job_recv_osd_resutl = client.job.fast_execute_script(job_recv_osd_kwargs)
    time.sleep(30)
    now = datetime.datetime.now()
    logger.info(u"当前时间：{}, OSD 扩容任务调用成功!".format(now))
    # 写 celery 操作记录
    celery_ip = "172.50.18.214"
    celery_opra_time = datetime.datetime.now()
    celery_opra_content = "OSD 扩容成功"
    Operations.objects.create(ip = celery_ip,celery_opra_time = celery_opra_time, celery_opra_content = celery_opra_content)


@periodic_task(run_every=crontab(minute='*/2', hour='*', day_of_week="*"))
def get_mon_state():
    # 写 celery 操作记录
    celery_ip = "172.50.18.214"
    celery_opra_time = datetime.datetime.now()
    celery_opra_content = "检测 MON 状态"
    Operations.objects.create(ip=celery_ip, celery_opra_time=celery_opra_time, celery_opra_content=celery_opra_content)

    # 检测 MON 状态
    user = "admin"
    client = get_client_by_user(user)
    # 检测 MON 状态的脚本为 20
    job_kwargs = {
        "bk_biz_id": 3,
        "script_id": 20,
        "script_timeout": 1000,
        "account": "root",
        "is_param_sensitive": 0,
        "script_type": 1,
        "ip_list": [
            {
                "bk_cloud_id": 0,
                "ip": "172.50.18.214"
            }
        ],
    }
    fast_job_excute_resutl = client.job.fast_execute_script(job_kwargs)

    job_instance_id = fast_job_excute_resutl["data"]["job_instance_id"]
    time.sleep(5)
    log_kwargs = {
        "bk_biz_id": 3,  # http://job.blueking.com/?scriptList&appId=3
        "job_instance_id": job_instance_id,  # 脚本管理 里 有个 3
    }

    get_job_instance_log_result = client.job.get_job_instance_log(log_kwargs)
    print get_job_instance_log_result
    # 处理脚本执行结果
    log_content = get_job_instance_log_result["data"][0]["step_results"][0]["ip_logs"][0]["log_content"]
    print log_content
    if "Down" in log_content:
        # set IP and  set host
        if "172.50.18.214" in log_content:
            ip = "172.50.18.214"
            type = "CEPH-1"
        elif "172.50.18.215" in log_content:
            ip = "172.50.18.215"
            type = "CEPH-2"
        elif "172.50.18.216" in log_content:
            ip = "172.50.18.216"
            type = "CEPH-3"

        alarm_time = datetime.datetime.now()
        alarm_content = log_content
        alarm_level = "ERROR"

        now = datetime.datetime.now()
        logger.info(u"当前时间：{}, celery get_mon_state 周期任务调用成功!".format(now))

        # 调用 recov_mon 开始进行 MON 自愈
        recov_mon()
        recv_time = datetime.datetime.now()
        recv_result = "成功"

        Alarm.objects.create(ip = ip,type = type, alarm_time = alarm_time, alarm_content = alarm_content, \
        alarm_level = alarm_level, recv_time = recv_time,recv_result = recv_result)


def recov_mon():
    # 写 celery 操作记录
    celery_ip = "172.50.18.214"
    celery_opra_time = datetime.datetime.now()
    celery_opra_content = "启动 MON 自愈"
    Operations.objects.create(ip=celery_ip, celery_opra_time=celery_opra_time, celery_opra_content=celery_opra_content)

    # 开始 MON 自愈操作
    user = "admin"
    client = get_client_by_user(user)
    # MON 自愈的脚本id 为 21
    job_recv_osd_kwargs = {
        "bk_biz_id": 3,
        "script_id": 21,
        "script_timeout": 1000,
        "account": "root",
        "is_param_sensitive": 0,
        "script_type": 1,
        "ip_list": [
            {
                "bk_cloud_id": 0,
                "ip": "172.50.18.214"
            }
        ],
    }
    print u"开始执行 MON 自愈脚本......"
    job_recv_osd_resutl = client.job.fast_execute_script(job_recv_osd_kwargs)
    time.sleep(20)
    now = datetime.datetime.now()
    logger.info(u"当前时间：{}, celery recovory_MON 任务调用成功!".format(now))
    # 写 celery 操作记录
    celery_ip = "172.50.18.214"
    celery_opra_time = datetime.datetime.now()
    celery_opra_content = "MON 自愈成功"
    Operations.objects.create(ip = celery_ip,celery_opra_time = celery_opra_time, celery_opra_content = celery_opra_content)