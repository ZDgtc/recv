# -*- coding: utf-8 -*-
from datetime import datetime
import base64


def get_job_instance_id(client, biz_id, ip, script_content):
    args = {
        'bk_biz_id': biz_id,
        'script_content': script_content,
        'account': 'root',
        'ip_list': [{
            'bk_cloud_id': 0,
            'ip': ip
        }]
    }
    resp = client.job.fast_execute_script(**args)
    if resp.get('result'):
        job_instance_id = resp.get('data').get('job_instance_id')
    else:
        job_instance_id = -1
    return resp.get('result'), job_instance_id


def get_job_log_content(client, biz_id, job_instance_id):
    args = {
        'bk_biz_id': biz_id,
        'job_instance_id': job_instance_id,
    }
    resp = client.job.get_job_instance_log(**args)
    is_finished = False
    log_content = ''
    if resp.get('result'):
        data = resp.get('data')[0]
        if data.get('is_finished'):
            is_finished = True
            log_content = data['step_results'][0]['ip_logs'][0]['log_content']
    return is_finished, log_content
