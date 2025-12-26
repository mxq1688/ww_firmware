#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EC800K/EG800K FOTA 升级测试脚本
基于 Quectel LTE Standard(A)系列 DFOTA 升级指导 V1.4

功能：
1. 串口连接测试
2. 模块基本信息查询
3. 网络状态检查
4. FOTA升级功能（带进度监听）

升级流程：
1. 查询当前版本 (AT+QGMR)
2. 发送升级指令 (AT+QFOTADL)
3. 监听进度上报 (+QIND: "FOTA","UPDATING",进度)
4. 等待升级完成 (+QIND: "FOTA","END",0)
5. 模组重启，验证新版本
"""

import serial
import serial.tools.list_ports
import time
import sys
import re
import threading
from typing import Optional, Tuple, Callable
from datetime import datetime

# ================== 配置区域 ==================
DEFAULT_PORT = "/dev/tty.usbserial-1420"  # macOS示例，请根据实际修改
DEFAULT_BAUDRATE = 115200
DEFAULT_TIMEOUT = 2  # 秒


def log(msg: str):
    """带时间戳的日志输出"""
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"[{timestamp}] {msg}")


class EC800KModem:
    """EC800K/EG800K 4G模块控制类"""

    def __init__(self, port: str = DEFAULT_PORT, baudrate: int = DEFAULT_BAUDRATE):
        self.port = port
        self.baudrate = baudrate
        self.serial: Optional[serial.Serial] = None
        self._stop_monitor = False
        self._monitor_thread: Optional[threading.Thread] = None
        self._progress_callback: Optional[Callable[[str, int], None]] = None

    def connect(self) -> bool:
        """连接串口"""
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=DEFAULT_TIMEOUT
            )
            log(f"✅ 串口连接成功: {self.port} @ {self.baudrate}bps")
            return True
        except serial.SerialException as e:
            log(f"❌ 串口连接失败: {e}")
            return False

    def disconnect(self):
        """断开串口"""
        self._stop_monitor = True
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=2)
        if self.serial and self.serial.is_open:
            self.serial.close()
            log("🔌 串口已断开")

    def send_at_command(self, cmd: str, timeout: float = 2.0, wait_ok: bool = True) -> Tuple[bool, str]:
        """
        发送AT命令并获取响应
        
        Args:
            cmd: AT命令（不需要加\\r\\n）
            timeout: 超时时间（秒）
            wait_ok: 是否等待OK响应
            
        Returns:
            (成功标志, 响应内容)
        """
        if not self.serial or not self.serial.is_open:
            return False, "串口未连接"

        # 清空缓冲区
        self.serial.reset_input_buffer()

        # 发送命令
        full_cmd = f"{cmd}\r\n"
        self.serial.write(full_cmd.encode('utf-8'))
        log(f"📤 发送: {cmd}")

        # 读取响应
        response = ""
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if self.serial.in_waiting > 0:
                chunk = self.serial.read(self.serial.in_waiting).decode('utf-8', errors='ignore')
                response += chunk
                
                # 检查是否收到完整响应
                if wait_ok and ("OK" in response or "ERROR" in response or "+CME ERROR" in response):
                    break
            time.sleep(0.05)

        response = response.strip()
        if response:
            log(f"📥 响应: {response}")
        
        success = "OK" in response if wait_ok else True
        return success, response

    # ================== 基本AT命令 ==================

    def test_at(self) -> bool:
        """测试AT通信"""
        success, _ = self.send_at_command("AT")
        return success

    def get_firmware_version(self) -> str:
        """
        获取固件版本 (使用AT+QGMR)
        返回格式如: EG800KEULCR07A07M04_01.300.01.300
        """
        success, resp = self.send_at_command("AT+QGMR")
        if success:
            # 解析版本号，跳过回显和OK
            lines = resp.split('\n')
            for line in lines:
                line = line.strip()
                # 版本格式: EG800KEULCR07A07M04_01.300.01.300
                if line and not line.startswith('AT') and line != 'OK':
                    return line
        return ""

    def get_module_info(self) -> dict:
        """获取模块信息"""
        info = {}
        
        # 制造商信息
        success, resp = self.send_at_command("ATI")
        if success:
            info['module_info'] = resp
        
        # 固件版本 (使用AT+QGMR)
        version = self.get_firmware_version()
        if version:
            info['firmware_version'] = version
            # 解析版本号
            match = re.search(r'(\d+\.\d+\.\d+\.\d+)$', version)
            if match:
                info['version_number'] = match.group(1)
            
        # IMEI
        success, resp = self.send_at_command("AT+GSN")
        if success:
            lines = resp.split('\n')
            for line in lines:
                line = line.strip()
                if line.isdigit() and len(line) == 15:
                    info['imei'] = line
                    break
                    
        # SIM卡状态
        success, resp = self.send_at_command("AT+CPIN?")
        if success:
            if "READY" in resp:
                info['sim_status'] = "已就绪"
            else:
                info['sim_status'] = resp
                
        return info

    def check_network_status(self) -> dict:
        """检查网络状态"""
        status = {}
        
        # 网络注册状态
        success, resp = self.send_at_command("AT+CREG?")
        if success:
            if "+CREG: " in resp:
                match = re.search(r'\+CREG:\s*\d+,(\d+)', resp)
                if match:
                    reg_status = int(match.group(1))
                    status['network_reg'] = {
                        0: "未注册",
                        1: "已注册(本地)",
                        2: "搜索中...",
                        3: "注册被拒绝",
                        4: "未知",
                        5: "已注册(漫游)"
                    }.get(reg_status, f"未知({reg_status})")
                    
        # 信号强度
        success, resp = self.send_at_command("AT+CSQ")
        if success:
            match = re.search(r'\+CSQ:\s*(\d+),', resp)
            if match:
                rssi = int(match.group(1))
                if rssi == 99:
                    status['signal'] = "未知或不可检测"
                else:
                    dbm = -113 + 2 * rssi
                    status['signal'] = f"RSSI={rssi} ({dbm}dBm)"
                    
        # PDP上下文状态
        success, resp = self.send_at_command("AT+CGACT?")
        if success:
            status['pdp_context'] = resp
            
        return status

    # ================== FOTA 相关命令 ==================

    def _monitor_fota_progress(self):
        """
        监听FOTA进度的后台线程
        解析 +QIND: "FOTA","UPDATING",进度 和 +QIND: "FOTA","END",结果码
        """
        buffer = ""
        
        while not self._stop_monitor:
            try:
                if self.serial and self.serial.is_open and self.serial.in_waiting > 0:
                    chunk = self.serial.read(self.serial.in_waiting).decode('utf-8', errors='ignore')
                    buffer += chunk
                    
                    # 按行处理
                    while '\n' in buffer:
                        line, buffer = buffer.split('\n', 1)
                        line = line.strip()
                        
                        if not line:
                            continue
                            
                        # 解析 +QIND: "FOTA","UPDATING",进度
                        match = re.search(r'\+QIND:\s*"FOTA"\s*,\s*"UPDATING"\s*,\s*(\d+)', line)
                        if match:
                            progress = int(match.group(1))
                            log(f"📊 升级进度: {progress}%")
                            if self._progress_callback:
                                self._progress_callback("UPDATING", progress)
                            continue
                        
                        # 解析 +QIND: "FOTA","END",结果码
                        match = re.search(r'\+QIND:\s*"FOTA"\s*,\s*"END"\s*,\s*(\d+)', line)
                        if match:
                            result = int(match.group(1))
                            if result == 0:
                                log("✅ FOTA升级完成!")
                            else:
                                log(f"❌ FOTA升级失败，错误码: {result}")
                            if self._progress_callback:
                                self._progress_callback("END", result)
                            continue
                        
                        # 解析其他 +QIND 消息
                        if "+QIND:" in line:
                            log(f"📨 {line}")
                            continue
                            
                        # 开机信息
                        if line in ["RDY", "+CFUN: 1"] or line.startswith("+CPIN:") or line.startswith("+QUSIM:"):
                            log(f"📨 开机信息: {line}")
                            
            except Exception as e:
                if not self._stop_monitor:
                    log(f"⚠️ 监听异常: {e}")
                    
            time.sleep(0.05)

    def fota_upgrade(self, url: str, auto_reset: int = 0, timeout: int = 50,
                     progress_callback: Optional[Callable[[str, int], None]] = None) -> Tuple[bool, str]:
        """
        执行FOTA升级
        
        基于实际升级日志，AT指令格式:
        AT+QFOTADL="URL",升级模式,超时时间
        
        Args:
            url: FOTA包下载地址 (HTTP/HTTPS)
            auto_reset: 升级模式 (0=手动重启, 1=自动重启)
            timeout: 超时时间（秒）
            progress_callback: 进度回调函数 callback(status, value)
                              status: "UPDATING" 或 "END"
                              value: 进度百分比 或 结果码
            
        Returns:
            (成功标志, 响应/错误信息)
        """
        # URL长度检查（文档规定最大700字符）
        if len(url) > 700:
            return False, "URL长度超过700字符限制"

        # 保存回调
        self._progress_callback = progress_callback

        log("\n" + "=" * 50)
        log("🔄 开始FOTA升级")
        log("=" * 50)
        
        # 1. 先查询当前版本
        log("\n[步骤1] 查询当前固件版本...")
        current_version = self.get_firmware_version()
        if current_version:
            log(f"📌 当前版本: {current_version}")
        else:
            log("⚠️ 无法获取当前版本")

        # 2. 检查网络状态
        log("\n[步骤2] 检查网络状态...")
        status = self.check_network_status()
        if status.get('network_reg') not in ["已注册(本地)", "已注册(漫游)"]:
            return False, f"网络未注册: {status.get('network_reg', '未知')}"
        log(f"✅ 网络已连接: {status.get('network_reg')}")
        if 'signal' in status:
            log(f"📶 信号强度: {status['signal']}")

        # 3. 发送FOTA升级指令
        log("\n[步骤3] 发送FOTA升级指令...")
        log(f"📎 URL: {url}")
        log(f"📎 升级模式: {'自动重启' if auto_reset == 1 else '手动重启'}")
        log(f"📎 超时时间: {timeout}秒")
        
        # AT+QFOTADL="URL",升级模式,超时时间
        cmd = f'AT+QFOTADL="{url}",{auto_reset},{timeout}'
        
        # 启动进度监听线程
        self._stop_monitor = False
        self._monitor_thread = threading.Thread(target=self._monitor_fota_progress, daemon=True)
        self._monitor_thread.start()
        
        # 发送命令
        success, resp = self.send_at_command(cmd, timeout=5)
        
        if not success:
            self._stop_monitor = True
            return False, f"指令发送失败: {resp}"
        
        log("✅ 指令发送成功，模组开始下载固件包...")
        log("\n[步骤4] 等待升级进度上报...")
        log("(模组会先下载固件包，然后多次重启进行升级)")
        
        return True, "FOTA升级已启动，请监听进度上报"

    def wait_for_fota_complete(self, max_wait: int = 300) -> Tuple[bool, int]:
        """
        等待FOTA升级完成
        
        Args:
            max_wait: 最大等待时间（秒）
            
        Returns:
            (成功标志, 结果码)
        """
        log(f"\n⏳ 等待升级完成（最长{max_wait}秒）...")
        
        result_received = False
        result_code = -1
        
        def on_progress(status: str, value: int):
            nonlocal result_received, result_code
            if status == "END":
                result_received = True
                result_code = value
        
        self._progress_callback = on_progress
        
        start_time = time.time()
        while time.time() - start_time < max_wait:
            if result_received:
                break
            time.sleep(0.5)
        
        self._stop_monitor = True
        
        if result_received:
            return result_code == 0, result_code
        else:
            return False, -1  # 超时

    def query_fota_status(self) -> Tuple[bool, str]:
        """查询FOTA状态"""
        return self.send_at_command("AT+QFOTADL?")


def list_serial_ports():
    """列出所有可用串口"""
    ports = serial.tools.list_ports.comports()
    print("\n📋 可用串口列表:")
    print("-" * 50)
    if not ports:
        print("  未发现可用串口")
    for port in ports:
        print(f"  {port.device}")
        print(f"    描述: {port.description}")
        print(f"    硬件ID: {port.hwid}")
        print()
    return ports


def run_basic_test(modem: EC800KModem):
    """运行基本测试"""
    print("\n" + "=" * 50)
    print("📡 EC800K/EG800K 基本测试")
    print("=" * 50)
    
    # AT测试
    print("\n[1/3] AT通信测试...")
    if modem.test_at():
        print("✅ AT通信正常")
    else:
        print("❌ AT通信失败")
        return False
    
    # 模块信息
    print("\n[2/3] 获取模块信息...")
    info = modem.get_module_info()
    for key, value in info.items():
        print(f"  {key}: {value}")
    
    # 网络状态
    print("\n[3/3] 检查网络状态...")
    status = modem.check_network_status()
    for key, value in status.items():
        print(f"  {key}: {value}")
    
    return True


def run_fota_test(modem: EC800KModem, url: str, auto_reset: int = 0, timeout: int = 50):
    """运行FOTA升级测试"""
    
    # 进度回调
    def on_progress(status: str, value: int):
        if status == "UPDATING":
            # 进度条
            bar_len = 30
            filled = int(bar_len * value / 100)
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"\r  [{bar}] {value}%", end="", flush=True)
        elif status == "END":
            print()  # 换行
    
    # 开始升级
    success, msg = modem.fota_upgrade(url, auto_reset, timeout, on_progress)
    
    if not success:
        log(f"❌ {msg}")
        return False
    
    # 等待完成
    success, result_code = modem.wait_for_fota_complete(max_wait=300)
    
    if success:
        log("\n[步骤5] 验证新版本...")
        time.sleep(5)  # 等待模组完全重启
        new_version = modem.get_firmware_version()
        if new_version:
            log(f"📌 新版本: {new_version}")
        log("✅ FOTA升级成功!")
    else:
        if result_code == -1:
            log("❌ 等待超时")
        else:
            log(f"❌ 升级失败，错误码: {result_code}")
            print_fota_error(result_code)
    
    return success


def print_fota_error(code: int):
    """打印FOTA错误码说明"""
    errors = {
        0: "升级成功",
        504: "升级失败",
        505: "包校验出错",
        506: "固件MD5检查错误",
        507: "包版本不匹配",
        552: "包项目名不匹配",
        553: "包基线名不匹配",
    }
    desc = errors.get(code, "未知错误")
    log(f"  错误说明: {desc}")


def print_error_codes():
    """打印FOTA错误码说明"""
    print("\n" + "=" * 50)
    print("📖 FOTA 错误码说明 (基于文档第6章)")
    print("=" * 50)
    
    print("\n【FOTA升级错误码】(+QIND: \"FOTA\",\"END\",<err>)")
    dfota_errors = {
        0: "升级成功",
        504: "升级失败",
        505: "包校验出错",
        506: "固件MD5检查错误",
        507: "包版本不匹配",
        552: "包项目名不匹配",
        553: "包基线名不匹配",
    }
    for code, desc in dfota_errors.items():
        print(f"  {code}: {desc}")
    
    print("\n【HTTP下载错误码】")
    http_errors = {
        0: "下载成功",
        701: "未知错误",
        702: "超时",
        703: "忙",
        711: "URL错误",
        714: "DNS错误",
        716: "Socket连接错误",
    }
    for code, desc in http_errors.items():
        print(f"  {code}: {desc}")
    
    print("\n【FTP下载错误码】")
    ftp_errors = {
        0: "下载成功",
        601: "未知错误",
        602: "超时",
        611: "打开文件失败",
        625: "登录失败",
    }
    for code, desc in ftp_errors.items():
        print(f"  {code}: {desc}")
    
    print("\n【+QIND URC上报说明】")
    print("  +QIND: \"FOTA\",\"HTTPSTART\"     - 开始HTTP下载")
    print("  +QIND: \"FOTA\",\"HTTPEND\",<err> - HTTP下载结束")
    print("  +QIND: \"FOTA\",\"START\"         - 开始升级")
    print("  +QIND: \"FOTA\",\"UPDATING\",<%>  - 升级进度(7%-96%)")
    print("  +QIND: \"FOTA\",\"END\",<err>     - 升级结束(0=成功)")


def main():
    """主函数"""
    print("=" * 50)
    print("🚀 EC800K/EG800K FOTA 升级测试工具")
    print("   基于 Quectel DFOTA升级指导 V1.4")
    print("=" * 50)
    
    # 列出可用串口
    ports = list_serial_ports()
    
    if len(sys.argv) < 2:
        print("\n使用方法:")
        print(f"  python {sys.argv[0]} <串口> [命令] [参数...]")
        print("\n命令:")
        print("  test               - 基本测试（默认）")
        print("  info               - 显示错误码说明")
        print("  version            - 仅查询固件版本")
        print("  fota URL [mode] [timeout]")
        print("                     - FOTA升级")
        print("                       mode: 0=手动重启(默认), 1=自动重启")
        print("                       timeout: 超时秒数(默认50)")
        print("\n示例:")
        print(f"  python {sys.argv[0]} /dev/tty.usbserial-1420 test")
        print(f"  python {sys.argv[0]} /dev/tty.usbserial-1420 version")
        print(f"  python {sys.argv[0]} COM3 fota \"http://server/fota.bin\" 0 50")
        return

    port = sys.argv[1]
    command = sys.argv[2] if len(sys.argv) > 2 else "test"
    
    if command == "info":
        print_error_codes()
        return
    
    # 创建模块实例并连接
    modem = EC800KModem(port=port)
    
    if not modem.connect():
        print("\n💡 提示: 请检查串口连接和权限")
        return
    
    try:
        if command == "test":
            run_basic_test(modem)
        elif command == "version":
            version = modem.get_firmware_version()
            if version:
                print(f"\n📌 固件版本: {version}")
            else:
                print("\n❌ 无法获取版本")
        elif command == "fota":
            if len(sys.argv) < 4:
                print("❌ 请提供FOTA包URL")
                print("   用法: python script.py <串口> fota <URL> [mode] [timeout]")
                print("   示例: python script.py COM3 fota \"http://server/fota.bin\" 0 50")
            else:
                url = sys.argv[3]
                auto_reset = int(sys.argv[4]) if len(sys.argv) > 4 else 0
                timeout = int(sys.argv[5]) if len(sys.argv) > 5 else 50
                run_fota_test(modem, url, auto_reset, timeout)
        else:
            print(f"❌ 未知命令: {command}")
            
    except KeyboardInterrupt:
        print("\n\n⚠️ 用户中断")
    finally:
        modem.disconnect()
    
    print("\n✨ 完成")


if __name__ == "__main__":
    main()
