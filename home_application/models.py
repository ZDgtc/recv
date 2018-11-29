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

from django.db import models


class IpList(models.Model):
    ip = models.CharField(max_length=64)
    type = models.CharField(max_length=10)
    auto_reboot = models.BooleanField(blank=True, null=True)
    ignore_seconds = models.IntegerField()
    last_alive_time = models.DateTimeField(blank=True, null=True)
    last_reboot_time = models.DateTimeField(blank=True, null=True)


class Alarm(models.Model):
    ip = models.CharField(max_length=64)
    type = models.CharField(max_length=64)
    alarm_time = models.DateTimeField()
    alarm_content = models.TextField()
    alarm_level = models.CharField(max_length=32)
    recv_time = models.DateTimeField(null=True, blank=True)
    recv_result = models.CharField(max_length=32, blank=True)


class Recv(models.Model):
    ip = models.CharField(max_length=64)
    celery_opra_time = models.DateTimeField()
    celery_opra_content = models.TextField()


