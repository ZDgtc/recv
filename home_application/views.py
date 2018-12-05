# -*- coding: utf-8 -*-
"""
Tencent is pleased to support the open source community by making 蓝鲸智云(BlueKing) available.
Copyright (C) 2017 THL A29 Limited, a Tencent company. All rights reserved.
Licensed under the MIT License (the "License"); you may not use this file except in compliance with the License.
You may obtain a copy of the License at http://opensource.org/licenses/MIT
Unless required by applicable law or agreed to in writing, software distributed under the License is distributed on
an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and limitations under the License.
"""

import json
import pdb

import time
import base64
from models import Alarm, Operations
from common.mymako import render_mako_context, render_json


def index(request):
    return render_mako_context(request, '/home_application/index.html')


def get_celery_record(request):
    records = Operations.objects.order_by('-id')[:5]
    print type(records)
    data = []
    for record in records:
        data.append({'celery_opra_time': record.celery_opra_time.strftime('%Y-%m-%d %H:%M:%S'), 'celery_opra_content': record.celery_opra_content})
    # #pdb.set_trace()
    # #print data
    result = {"data": data}
    return render_json(result)


def get_alarm_num(request):
    ceph_alarm_num = Alarm.objects.filter(type__icontains="CEPH", alarm_level='ERROR').count()
    ceph_opera_num = Alarm.objects.filter(type__icontains="CEPH", recv_time__isnull=False).count()
    ceph_recv_succeed_num = Alarm.objects.filter(type__icontains="CEPH", recv_result__icontains="成功").count()
    openstack_alarm_num = Alarm.objects.filter(type__icontains="OpenStack", alarm_level='ERROR').count()
    openstack_opera_num = Alarm.objects.filter(type__icontains="OpenStack", recv_time__isnull=False).count()
    openstack_recv_succeed_num = Alarm.objects.filter(type__icontains="OpenStack", recv_result__icontains="成功").count()

    result = {
        "data": {"ceph_alarm_num": ceph_alarm_num, "ceph_opera_num": ceph_opera_num,
                 "ceph_recv_succeed_num": ceph_recv_succeed_num,
                 "openstack_alarm_num": openstack_alarm_num, "openstack_opera_num": openstack_opera_num,
                 "openstack_recv_succeed_num": openstack_recv_succeed_num
                 }
    }

    return render_json(result)


def get_recv_records(request):
    records = Alarm.objects.filter(recv_time__isnull=False, recv_result__icontains="成功").reverse()[:5]
    data = []
    for record in records:
        data.append(
            {
                "ip": record.ip,
                "type": record.type,
                "alarm_time": record.alarm_time.strftime('%Y-%m-%d %H:%M:%S'),
                "alarm_content": record.alarm_content,
                "recv_time": record.recv_time.strftime('%Y-%m-%d %H:%M:%S'),
                "recv_result": record.recv_result
             }
        )
    result = {
        "data": data
    }
    return render_json(result)


def search(request):
    data = []
    alarm_type = request.GET.get('type', None)
    keyword = request.GET.get('keyword', None)
    if alarm_type == 'all' or keyword is None:
        records = Alarm.objects.all().order_by("-id")[:5]
    else:
        records = Alarm.objects.filter(type__contains=alarm_type, alarm_content__icontains=keyword).order_by("-id")[:5]
    for record in records:
        data.append(
            {
                "ip": record.ip,
                "type": record.type,
                "alarm_time": record.alarm_time.strftime('%Y-%m-%d %H:%M:%S'),
                "alarm_content": record.alarm_content,
                "recv_time": record.recv_time.strftime('%Y-%m-%d %H:%M:%S') if record.recv_time is not None else record.recv,
                "recv_result": record.recv_result
            }
        )
    result = {
        "data": data
    }
    return render_json(result)


def alarm_page(request):
    return render_mako_context(request, '/home_application/alarm.html')
