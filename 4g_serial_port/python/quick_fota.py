# -*- coding: utf-8 -*-
"""
EC800K/EG800K FOTA 升级/降级工具

用法:
    python quick_fota.py <串口> <URL> [mode] [timeout]

参数:
    串口     - 串口号，如 COM8
    URL      - FOTA固件下载地址 (HTTP，不支持HTTPS)
    mode     - 0=不自动重启, 1=自动重启 (默认1)
    timeout  - 超时时间秒数 (默认50)

示例:
    python quick_fota.py COM8 "http://example.com/firmware.mini_1"
    python quick_fota.py COM8 "http://example.com/firmware.mini_1" 1 50
"""
import serial
import time
import sys
import argparse


def send_at(ser, cmd, timeout=2, verbose=True):
    """发送AT指令并返回响应"""
    if verbose:
        print(f">>> {cmd}")
    ser.reset_input_buffer()
    ser.write(f"{cmd}\r\n".encode())
    time.sleep(timeout)
    resp = ser.read(ser.in_waiting or 1000).decode('utf-8', errors='ignore')
    if verbose:
        print(f"<<< {resp.strip()}")
    return resp


def fota_upgrade(port, url, mode=1, timeout=50):
    """执行FOTA升级"""
    print("=" * 50)
    print("EC800K FOTA 升级工具")
    print("=" * 50)
    
    # 连接串口
    print(f"\n[1] 打开串口 {port}...")
    try:
        ser = serial.Serial(port, 115200, timeout=2)
        print(f"    OK: {port} 已打开")
    except Exception as e:
        print(f"    错误: {e}")
        return False
    
    try:
        # 测试AT
        print("\n[2] 测试AT...")
        resp = send_at(ser, "AT", 1)
        if "OK" not in resp:
            print("    错误: AT指令无响应")
            return False
        
        # 获取固件版本
        print("\n[3] 获取固件版本...")
        send_at(ser, "AT+QGMR", 1)
        
        # 检查网络
        print("\n[4] 检查网络状态...")
        send_at(ser, "AT+CREG?", 1)
        send_at(ser, "AT+CSQ", 1)
        
        # 配置PDP上下文
        print("\n[5] 配置PDP上下文...")
        send_at(ser, 'AT+QICSGP=1,1,"cmnet","","",1', 2)
        
        # 激活PDP上下文
        print("\n[6] 激活PDP上下文...")
        send_at(ser, "AT+QIACT=1", 3)
        send_at(ser, "AT+QIACT?", 1)
        
        # 启动FOTA
        print("\n[7] 启动FOTA升级...")
        print(f"    URL: {url}")
        print(f"    模式: {mode} (0=不重启, 1=自动重启)")
        print(f"    超时: {timeout}秒")
        cmd = f'AT+QFOTADL="{url}",{mode},{timeout}'
        send_at(ser, cmd, 3)
        
        # 监控URC消息
        print("\n[8] 监控升级进度 (最长180秒)...")
        start = time.time()
        while time.time() - start < 180:
            if ser.in_waiting > 0:
                data = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                if data.strip():
                    for line in data.strip().split('\n'):
                        line = line.strip()
                        if line:
                            print(f"<<< {line}")
                            if '"FOTA","END",0' in line:
                                print("\n*** FOTA升级成功! ***")
                                return True
                            elif '"FOTA","END",' in line:
                                print("\n*** FOTA升级失败! ***")
                                return False
            time.sleep(0.3)
        
        print("\n*** 超时，未检测到升级完成 ***")
        return False
        
    finally:
        ser.close()
        print("\n" + "=" * 50)
        print("完成")


def main():
    parser = argparse.ArgumentParser(
        description='EC800K/EG800K FOTA 升级工具',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
示例:
  升级: python quick_fota.py COM8 "http://server/signed_A-B.mini_1"
  降级: python quick_fota.py COM8 "http://server/signed_B-A.mini_1"
        '''
    )
    parser.add_argument('port', help='串口号，如 COM8')
    parser.add_argument('url', help='FOTA固件URL (HTTP)')
    parser.add_argument('mode', nargs='?', type=int, default=1,
                        help='升级后是否自动重启: 0=否, 1=是 (默认1)')
    parser.add_argument('timeout', nargs='?', type=int, default=50,
                        help='超时时间秒数 (默认50)')
    
    args = parser.parse_args()
    
    success = fota_upgrade(args.port, args.url, args.mode, args.timeout)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
