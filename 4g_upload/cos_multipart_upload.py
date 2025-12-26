#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
腾讯云 COS 分片上传工具
支持使用临时凭证进行分片上传

使用方法:
    python3 cos_multipart_upload.py <文件路径>

示例:
    python3 cos_multipart_upload.py /path/to/file.opus
"""

import hmac
import hashlib
import time
import requests
import os
import sys
import re
from datetime import datetime

# ============================================
# 配置区域 - 根据实际情况修改
# ============================================

# COS 配置（南京区域）
COS_HOST = "tc-nj-ticnote-1324023246.cos.ap-nanjing.myqcloud.com"
COS_BUCKET = "tc-nj-ticnote-1324023246"
COS_REGION = "ap-nanjing"

# 临时凭证（需要从服务器获取）
TMP_SECRET_ID = "AKIDl-FbhSS6gNZJbixhI1LbnRrQaRoclgqdIelCb7ENe8W2AMdpM6XPZfVDiSEk4XIk"
TMP_SECRET_KEY = ""  # ⚠️ 需要填入 tmpSecretKey
SESSION_TOKEN = "LdGQgp6aox07bKdpgpSVz5QvM6a0UjMa8cd7268321678bc32570ab86eb0d3a7b43ZWbZLOguOz0CGtAy9aQ9tPwkO9AKtEBI0kGSjf76p4gNaA81X5HVofbH_r-dK0j1y2gys4jSnoIhXvCdG1JC--WpIH18Bu6ajmiDVEWrNqrdhr3WAUTzRoDaQ7ZH1LdXS4E9pS9qD44K2Xgp2r2jg5E7nGK7Q8D7f5dT0PQsAPmMJ98WvMCAl-I7lyYdUZKw4BuHBolSMso-Z6-driWLPohfxeiAy6vZ8vgjoChjAC3hLunC47tn-leQxWeF4qgFJ65YRitD4WfJed5-PNxLnhmkIoNz0xAA8-9OAGzX8zvLwBApNKa3LFLCCIEk1t02qNF6M4qVIB6MgbksCRq0VMiY_3CANyrO5_NRA9KrnX83SfTHBf9TigApqUxR8nqkuQ_y6swTjEGjSYzOvq4ncUGITYpT5nGSjGCiUkYRCl2_Ld2jnarVNcHx4n2fx1u4OHqgvU8XeQcFNrDyGKfftzUewH8e8hW7TO4vn9qpyOLy9TABGJKUBternBT7gQX8hiX93LbUH9dCXfAUeuthtW9q2waB5wBScSSqY7TSmGsgKn_KC2ML1xwoniVRUeW0xTIc_DgmJ8VFm0UwknRg"

# 上传配置
CHUNK_SIZE = 32 * 1024  # 32KB 分片大小（适合 4G 模块）
TIMEOUT = 60  # 请求超时时间（秒）


def generate_cos_signature(method, uri_pathname, http_parameters="", http_headers="", 
                           secret_id=None, secret_key=None, expire_seconds=3600):
    """
    生成腾讯云 COS 签名 (V5 版本)
    
    参数:
        method: HTTP 方法 (get/post/put/delete)
        uri_pathname: URI 路径，如 /ticnote_rec/file.opus
        http_parameters: URL 参数，如 uploads= 或 partNumber=1&uploadId=xxx
        http_headers: HTTP 头，如 host=xxx.cos.xxx.myqcloud.com
        secret_id: 密钥 ID
        secret_key: 密钥 Key
        expire_seconds: 签名有效期（秒）
    
    返回:
        签名字符串
    """
    if secret_id is None:
        secret_id = TMP_SECRET_ID
    if secret_key is None:
        secret_key = TMP_SECRET_KEY
    
    # Step 1: 生成 KeyTime
    current_time = int(time.time())
    key_time = f"{current_time};{current_time + expire_seconds}"
    
    # Step 2: 生成 SignKey
    sign_key = hmac.new(
        secret_key.encode('utf-8'),
        key_time.encode('utf-8'),
        hashlib.sha1
    ).hexdigest()
    
    # Step 3: 生成 HttpString
    http_string = f"{method.lower()}\n{uri_pathname}\n{http_parameters}\n{http_headers}\n"
    
    # Step 4: 生成 StringToSign
    sha1_http_string = hashlib.sha1(http_string.encode('utf-8')).hexdigest()
    string_to_sign = f"sha1\n{key_time}\n{sha1_http_string}\n"
    
    # Step 5: 生成 Signature
    signature = hmac.new(
        sign_key.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        hashlib.sha1
    ).hexdigest()
    
    # Step 6: 生成 Authorization
    # 从 http_headers 提取 header list
    header_list = ""
    if http_headers:
        headers = http_headers.split('&')
        header_names = [h.split('=')[0] for h in headers if '=' in h]
        header_list = ';'.join(sorted(header_names))
    
    # 从 http_parameters 提取 param list
    param_list = ""
    if http_parameters:
        params = http_parameters.split('&')
        param_names = [p.split('=')[0] for p in params if '=' in p or p.endswith('=')]
        # 处理空值参数如 "uploads="
        param_names = [p.rstrip('=') if p.endswith('=') else p for p in param_names]
        param_list = ';'.join(sorted([p.split('=')[0] for p in http_parameters.split('&')]))
    
    authorization = (
        f"q-sign-algorithm=sha1"
        f"&q-ak={secret_id}"
        f"&q-sign-time={key_time}"
        f"&q-key-time={key_time}"
        f"&q-header-list={header_list}"
        f"&q-url-param-list={param_list}"
        f"&q-signature={signature}"
    )
    
    return authorization


def init_multipart_upload(object_key, host=None, session_token=None):
    """
    初始化分片上传
    
    返回:
        upload_id: 上传 ID
    """
    if host is None:
        host = COS_HOST
    if session_token is None:
        session_token = SESSION_TOKEN
    
    uri = f"/{object_key}"
    url = f"https://{host}{uri}?uploads"
    
    # 生成签名
    authorization = generate_cos_signature(
        method="post",
        uri_pathname=uri,
        http_parameters="uploads=",
        http_headers=f"host={host}"
    )
    
    headers = {
        "Host": host,
        "Authorization": authorization,
        "x-cos-security-token": session_token
    }
    
    print(f"\n{'='*60}")
    print("Step 1: 初始化分片上传")
    print(f"{'='*60}")
    print(f"URL: {url}")
    
    response = requests.post(url, headers=headers, timeout=TIMEOUT)
    
    print(f"HTTP 状态码: {response.status_code}")
    
    if response.status_code == 200:
        # 解析 UploadId
        match = re.search(r'<UploadId>(.+?)</UploadId>', response.text)
        if match:
            upload_id = match.group(1)
            print(f"✅ 初始化成功！")
            print(f"UploadId: {upload_id}")
            return upload_id
    
    print(f"❌ 初始化失败！")
    print(f"响应: {response.text}")
    return None


def upload_part(object_key, upload_id, part_number, data, host=None, session_token=None):
    """
    上传分片
    
    返回:
        etag: 分片的 ETag
    """
    if host is None:
        host = COS_HOST
    if session_token is None:
        session_token = SESSION_TOKEN
    
    uri = f"/{object_key}"
    url = f"https://{host}{uri}?partNumber={part_number}&uploadId={upload_id}"
    
    # 生成签名 - 注意参数需要按字母顺序排列
    authorization = generate_cos_signature(
        method="put",
        uri_pathname=uri,
        http_parameters=f"partnumber={part_number}&uploadid={upload_id}",
        http_headers=f"host={host}"
    )
    
    headers = {
        "Host": host,
        "Authorization": authorization,
        "x-cos-security-token": session_token,
        "Content-Type": "application/octet-stream"
    }
    
    print(f"\n上传分片 {part_number}，大小: {len(data)} 字节 ({len(data)/1024:.1f} KB)")
    
    response = requests.put(url, headers=headers, data=data, timeout=TIMEOUT)
    
    if response.status_code == 200:
        etag = response.headers.get('ETag', '')
        print(f"✅ 分片 {part_number} 上传成功，ETag: {etag}")
        return etag
    
    print(f"❌ 分片 {part_number} 上传失败！")
    print(f"HTTP 状态码: {response.status_code}")
    print(f"响应: {response.text}")
    return None


def complete_multipart_upload(object_key, upload_id, parts, host=None, session_token=None):
    """
    完成分片上传
    
    参数:
        parts: [(part_number, etag), ...] 列表
    """
    if host is None:
        host = COS_HOST
    if session_token is None:
        session_token = SESSION_TOKEN
    
    uri = f"/{object_key}"
    url = f"https://{host}{uri}?uploadId={upload_id}"
    
    # 生成签名
    authorization = generate_cos_signature(
        method="post",
        uri_pathname=uri,
        http_parameters=f"uploadid={upload_id}",
        http_headers=f"host={host}"
    )
    
    # 构建 XML
    parts_xml = ""
    for part_number, etag in parts:
        parts_xml += f"""  <Part>
    <PartNumber>{part_number}</PartNumber>
    <ETag>{etag}</ETag>
  </Part>
"""
    
    complete_xml = f"""<CompleteMultipartUpload>
{parts_xml}</CompleteMultipartUpload>"""
    
    headers = {
        "Host": host,
        "Authorization": authorization,
        "x-cos-security-token": session_token,
        "Content-Type": "application/xml"
    }
    
    print(f"\n{'='*60}")
    print("Step 3: 完成分片上传")
    print(f"{'='*60}")
    
    response = requests.post(url, headers=headers, data=complete_xml, timeout=TIMEOUT)
    
    print(f"HTTP 状态码: {response.status_code}")
    
    if response.status_code == 200:
        print(f"✅ 上传完成！")
        print(f"响应:\n{response.text}")
        return True
    
    print(f"❌ 完成上传失败！")
    print(f"响应: {response.text}")
    return False


def upload_file(file_path, object_key=None, chunk_size=None):
    """
    上传文件到 COS（分片上传）
    
    参数:
        file_path: 本地文件路径
        object_key: COS 对象键（默认使用文件名）
        chunk_size: 分片大小（默认 CHUNK_SIZE）
    """
    if chunk_size is None:
        chunk_size = CHUNK_SIZE
    
    if object_key is None:
        object_key = f"ticnote_rec/{os.path.basename(file_path)}"
    
    # 读取文件
    with open(file_path, 'rb') as f:
        file_data = f.read()
    
    file_size = len(file_data)
    
    print(f"\n{'='*60}")
    print("腾讯云 COS 分片上传")
    print(f"{'='*60}")
    print(f"文件: {file_path}")
    print(f"大小: {file_size} 字节 ({file_size/1024:.1f} KB)")
    print(f"目标: {object_key}")
    print(f"分片大小: {chunk_size} 字节 ({chunk_size/1024:.1f} KB)")
    print(f"COS 地址: https://{COS_HOST}/{object_key}")
    
    # Step 1: 初始化分片上传
    upload_id = init_multipart_upload(object_key)
    if not upload_id:
        return False
    
    # Step 2: 上传分片
    print(f"\n{'='*60}")
    print("Step 2: 上传分片")
    print(f"{'='*60}")
    
    parts = []
    part_number = 1
    offset = 0
    
    while offset < file_size:
        # 获取当前分片数据
        chunk_data = file_data[offset:offset + chunk_size]
        
        # 上传分片
        etag = upload_part(object_key, upload_id, part_number, chunk_data)
        if not etag:
            print(f"❌ 上传中断！")
            return False
        
        parts.append((part_number, etag))
        offset += chunk_size
        part_number += 1
    
    print(f"\n共上传 {len(parts)} 个分片")
    
    # Step 3: 完成上传
    success = complete_multipart_upload(object_key, upload_id, parts)
    
    if success:
        print(f"\n{'='*60}")
        print("🎉 文件上传成功！")
        print(f"{'='*60}")
        print(f"文件地址: https://{COS_HOST}/{object_key}")
        return True
    
    return False


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("用法: python3 cos_multipart_upload.py <文件路径> [对象键]")
        print("示例: python3 cos_multipart_upload.py /path/to/file.opus")
        print("      python3 cos_multipart_upload.py /path/to/file.opus custom/path/file.opus")
        sys.exit(1)
    
    file_path = sys.argv[1]
    object_key = sys.argv[2] if len(sys.argv) > 2 else None
    
    if not os.path.exists(file_path):
        print(f"❌ 文件不存在: {file_path}")
        sys.exit(1)
    
    try:
        success = upload_file(file_path, object_key)
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ 上传失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

