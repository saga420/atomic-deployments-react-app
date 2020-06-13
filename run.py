#!/usr/bin/python

import os
import boto3
import pathlib
import datetime
import subprocess
import mimetypes
import json
import sys

s3_client = boto3.client('s3')
cloudfront_client = boto3.client('cloudfront')

bucket = sys.argv[1]
log_bucket = sys.argv[2]
cloudfront_distribution_id = sys.argv[3]
build_dir = sys.argv[4]


# python atomic-deployments/run.py test-landing-page-deploy test-landing-page-deploy-log dev E1ATI9M3QB3Q0H
# bucket="test-landing-page-deploy"
# log_bucket = "test-landing-page-deploy-log"
# cloudfront_distribution_id = 'E1ATI9M3QB3Q0H'

'''
set .map files as private
'''


def get_file_acl(file):
    if pathlib.Path(file).suffix == '.map':
        return "private"
    return "public-read"


'''
get file mime
'''


def get_file_content_type(file):
    file_mime = mimetypes.guess_type(file)[0]
    return str(file_mime)


'''
set rollback version and current version
'''


def set_version(version, bucket):
    try:
        s3_client.copy_object(ACL='public-read', Bucket=bucket, CopySource=bucket + "/current.txt", Key="rollback.txt")
    except Exception as e:
        s3_client.put_object(ACL='public-read', Body=version, Bucket=bucket, Key="current.txt",
                                     ContentType='plain/text')
        log(msg=str(e), error=True)
        return False

'''
get rollback version
'''


def get_rollback_version(bucket):
    try:
        object = s3_client.get_object(Bucket=bucket, Key="rollback.txt")
        return object['Body'].read().decode('utf-8')
    except Exception as e:
        log(msg=str(e), error=True)
        return False


'''
upload dir to s3
'''


def sync_s3(destination, local, bucket):
    deploy_env = get_git_revision_branch_name()
    local_directory = os.path.abspath(local)
    # enumerate local files recursively
    for root, dirs, files in os.walk(local_directory):

        for filename in files:

            # construct the full local path
            local_path = os.path.join(root, filename)

            relative_path = os.path.relpath(local_path, local_directory)
            s3_path = os.path.join(destination, relative_path)

            # relative_path = os.path.relpath(os.path.join(root, filename))

            print('Searching "%s" in "%s"' % (s3_path, bucket))
            try:
                s3_client.head_object(Bucket=bucket, Key=s3_path)
                print("Path found on S3! Skipping %s..." % s3_path)

                # try:
                # client.delete_object(Bucket=bucket, Key=s3_path)
                # except:
                # print "Unable to delete %s..." % s3_path
            except:
                print("Uploading %s..." % s3_path)
                acl = get_file_acl(local_path)
                s3_client.upload_file(local_path, bucket, s3_path,
                                      ExtraArgs={'ACL': acl, 'ContentType': get_file_content_type(local_path),
                                                 'Metadata': {'upload_at': get_current_timestamp(),
                                                              'env': deploy_env,
                                                              'git_hash': get_git_revision_short_hash()}})  # public-read


'''
get cloudfront config object
'''


def get_cloudfront_config(cloudfront_distribution_id):
    try:
        response = cloudfront_client.get_distribution_config(
            Id=cloudfront_distribution_id
        )
        return response
    except Exception as e:
        log(msg=str(e), error=True)
        return None


'''
change cloudfront path to version path
'''


def change_origin_path(cloudfront_distribution_id, path_name):
    response = get_cloudfront_config(cloudfront_distribution_id)

    log("change_origin_path:get_cloudfront_config \n" + json.dumps(response, default=str))

    if response is None:
        return False

    items = []

    for item in response['DistributionConfig']['Origins']['Items']:
        item['OriginPath'] = "/" + path_name
        items.append(item)

    response['DistributionConfig']['Origins']['Items'] = items

    print(items)

    try:
        update_distribution_response = cloudfront_client.update_distribution(
            DistributionConfig=response['DistributionConfig'],
            Id=cloudfront_distribution_id,
            IfMatch=response['ETag']
        )
        log("change_origin_path:cloudfront_client.update_distribution \n" + json.dumps(
            update_distribution_response, default=str))
        return update_distribution_response['Distribution']['DistributionConfig']['Origins']['Items']
    except Exception as e:
        log(msg=str(e), error=True)
        return False


'''
get git repo hash
'''


def get_git_revision_hash():
    hash = subprocess.check_output(['git', 'rev-parse', 'HEAD']).decode().strip('\n')
    return str(hash)


def get_git_revision_branch_name():
    branch_name = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD']).decode().strip('\n')
    return str(branch_name)


'''
get git repo short hash 
'''


def get_git_revision_short_hash():
    hash = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD']).decode().strip('\n')
    return str(hash)


def waiter_deployed(cloudfront_distribution_id):
    try:
        waiter = cloudfront_client.get_waiter('distribution_deployed')
        response = waiter.wait(
            Id=cloudfront_distribution_id,
            WaiterConfig={
                'Delay': 50,
                'MaxAttempts': 35
            }
        )
        log("waiter_deployed:response\n" + json.dumps(response, default=str))
        return None
    except Exception as e:
        log(msg=str(e), error=True)
        return False


def waiter_invalidation_completed(cloudfront_distribution_id, invalidation_id):
    try:
        waiter = cloudfront_client.get_waiter('invalidation_completed')
        response = waiter.wait(
            DistributionId=cloudfront_distribution_id,
            Id=invalidation_id,
        )
        log("waiter_invalidation_completed:response\n" + json.dumps(response, default=str))
        return None
    except Exception as e:
        log(msg=str(e), error=True)
        return False


def get_current_timestamp():
    ts = datetime.datetime.now().timestamp()
    return str(ts)


def invalidate_cache(cloudfront_distribution_id):
    try:
        response = cloudfront_client.create_invalidation(
            DistributionId=cloudfront_distribution_id,
            InvalidationBatch={
                'Paths': {
                    'Quantity': 1,
                    'Items': [
                        '/*',
                    ]
                },
                'CallerReference': get_current_timestamp()
            }
        )
        log("invalidate_cache:response\n" + json.dumps(response, default=str))
        return response['Invalidation']
    except Exception as e:
        log(msg=str(e), error=True)
        return False


def deploy(cloudfront_distribution_id, git_hash=None):
    log("start: deploy flow")
    is_rollback = 0
    if git_hash is None:
        git_hash = get_git_revision_hash()
    else:
        is_rollback = 1
        log("type: rollback")

    log("Current Version: " + git_hash)

    if is_rollback == 0:
        sync_s3(git_hash, build_dir, bucket)

    log("Change Origin Path for Cloudfront: " + cloudfront_distribution_id)
    change_origin = change_origin_path(cloudfront_distribution_id, git_hash)
    if change_origin is False:
        log("Error: Change Origin Path fail")
        exit(1)

    log("Waiting for deployed: " + cloudfront_distribution_id)
    waiting_deployed = waiter_deployed(cloudfront_distribution_id)
    if waiting_deployed is False:
        log("Error: waiting_deployed fail")
        exit(1)

    log("Create an invalidate for Clean Cache: " + cloudfront_distribution_id)
    invalidation_response = invalidate_cache(cloudfront_distribution_id)
    log("Invalidate Id: " + invalidation_response['Id'])
    print(invalidation_response)
    if invalidation_response is not False:
        log("Waiting for invalidation completed: " + cloudfront_distribution_id)
        waiter_invalidation_completed(cloudfront_distribution_id, invalidation_response['Id'])

    log("Set Version: " + git_hash)
    set_version(git_hash, bucket)
    log("done: deploy flow")


def log(msg='', error=False):
    message = msg + "\n" + get_current_timestamp() + "\n" + "=============== \n"
    write_log_file('log.txt', message)
    print(message)
    return


def write_log_file(file, log):
    with open(file, "a") as l:
        l.write(log)


def upload_log():
    deploy_env = get_git_revision_branch_name()
    abs_file = os.path.abspath("log.txt")
    object_key = "deploy-logs/" + deploy_env + "/" + get_git_revision_hash() + "-" + get_current_timestamp() + ".txt"
    try:
        s3_client.upload_file(abs_file, log_bucket, object_key,
                              ExtraArgs={'ACL': 'private', 'ContentType': get_file_content_type(abs_file),
                                         'Metadata': {'deploy_at': get_current_timestamp()}})
        if os.path.exists(abs_file):
            os.remove(abs_file)
    except Exception as e:
        log(msg=str(e), error=True)


def run():
    deploy(cloudfront_distribution_id)
    upload_log()


if __name__ == '__main__':
    run()
