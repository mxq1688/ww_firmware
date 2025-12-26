//! EC800K/EG800K FOTA 升级测试脚本 - Rust版
//! 基于 Quectel LTE Standard(A)系列 DFOTA 升级指导 V1.4
//!
//! 升级流程：
//! 1. 查询当前版本 (AT+QGMR)
//! 2. 发送升级指令 (AT+QFOTADL="URL",mode,timeout)
//! 3. 监听进度上报 (+QIND: "FOTA","UPDATING",进度)
//! 4. 等待升级完成 (+QIND: "FOTA","END",0)
//!
//! 依赖: cargo add serialport regex chrono

use chrono::Local;
use regex::Regex;
use serialport::{available_ports, SerialPort};
use std::collections::HashMap;
use std::env;
use std::io::{Read, Write};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

const DEFAULT_BAUDRATE: u32 = 115200;
const AT_TIMEOUT: Duration = Duration::from_secs(2);

/// 带时间戳的日志
fn log(msg: &str) {
    let timestamp = Local::now().format("%H:%M:%S%.3f");
    println!("[{}] {}", timestamp, msg);
}

/// FOTA状态
struct FotaState {
    complete: bool,
    result: i32,
}

/// EC800K 模块控制结构
struct EC800KModem {
    port: Option<Box<dyn SerialPort>>,
    port_path: String,
    baud_rate: u32,
    stop_monitor: Arc<Mutex<bool>>,
    fota_state: Arc<Mutex<FotaState>>,
}

impl EC800KModem {
    fn new(port_path: &str, baud_rate: u32) -> Self {
        EC800KModem {
            port: None,
            port_path: port_path.to_string(),
            baud_rate,
            stop_monitor: Arc::new(Mutex::new(false)),
            fota_state: Arc::new(Mutex::new(FotaState {
                complete: false,
                result: -1,
            })),
        }
    }

    fn connect(&mut self) -> Result<(), String> {
        match serialport::new(&self.port_path, self.baud_rate)
            .timeout(AT_TIMEOUT)
            .open()
        {
            Ok(port) => {
                self.port = Some(port);
                log(&format!(
                    "✅ 串口连接成功: {} @ {}bps",
                    self.port_path, self.baud_rate
                ));
                Ok(())
            }
            Err(e) => Err(format!("串口连接失败: {}", e)),
        }
    }

    fn disconnect(&mut self) {
        *self.stop_monitor.lock().unwrap() = true;
        if self.port.is_some() {
            self.port = None;
            log("🔌 串口已断开");
        }
    }

    fn send_at_command(&mut self, cmd: &str, timeout: Duration) -> (bool, String) {
        log(&format!("📤 发送: {}", cmd));

        let port = match &mut self.port {
            Some(p) => p,
            None => return (false, "串口未连接".to_string()),
        };

        // 发送命令
        let cmd_bytes = format!("{}\r\n", cmd);
        if let Err(e) = port.write_all(cmd_bytes.as_bytes()) {
            return (false, format!("发送失败: {}", e));
        }

        // 读取响应
        let mut response = String::new();
        let mut buf = [0u8; 256];
        let start = Instant::now();

        while start.elapsed() < timeout {
            match port.read(&mut buf) {
                Ok(n) if n > 0 => {
                    response.push_str(&String::from_utf8_lossy(&buf[..n]));
                    if response.contains("OK") || response.contains("ERROR") {
                        break;
                    }
                }
                _ => {
                    thread::sleep(Duration::from_millis(50));
                }
            }
        }

        let response = response.trim().to_string();
        if !response.is_empty() {
            log(&format!("📥 响应: {}", response));
        }

        let success = response.contains("OK");
        (success, response)
    }

    fn test_at(&mut self) -> bool {
        let (success, _) = self.send_at_command("AT", AT_TIMEOUT);
        success
    }

    fn get_firmware_version(&mut self) -> String {
        // 使用 AT+QGMR 查询版本
        let (success, resp) = self.send_at_command("AT+QGMR", AT_TIMEOUT);
        if success {
            for line in resp.lines() {
                let line = line.trim();
                // 版本格式: EG800KEULCR07A07M04_01.300.01.300
                if !line.is_empty() && !line.starts_with("AT") && line != "OK" {
                    return line.to_string();
                }
            }
        }
        String::new()
    }

    fn get_module_info(&mut self) -> HashMap<String, String> {
        let mut info = HashMap::new();

        // 固件版本 (使用AT+QGMR)
        let version = self.get_firmware_version();
        if !version.is_empty() {
            info.insert("firmware_version".to_string(), version.clone());
            let re = Regex::new(r"(\d+\.\d+\.\d+\.\d+)$").unwrap();
            if let Some(m) = re.find(&version) {
                info.insert("version_number".to_string(), m.as_str().to_string());
            }
        }

        // IMEI
        let (success, resp) = self.send_at_command("AT+GSN", AT_TIMEOUT);
        if success {
            let re = Regex::new(r"\d{15}").unwrap();
            if let Some(m) = re.find(&resp) {
                info.insert("imei".to_string(), m.as_str().to_string());
            }
        }

        // SIM卡状态
        let (success, resp) = self.send_at_command("AT+CPIN?", AT_TIMEOUT);
        if success {
            if resp.contains("READY") {
                info.insert("sim_status".to_string(), "已就绪".to_string());
            } else {
                info.insert("sim_status".to_string(), resp);
            }
        }

        info
    }

    fn check_network_status(&mut self) -> HashMap<String, String> {
        let mut status = HashMap::new();

        // 网络注册状态
        let (success, resp) = self.send_at_command("AT+CREG?", AT_TIMEOUT);
        if success {
            let re = Regex::new(r"\+CREG:\s*\d+,(\d+)").unwrap();
            if let Some(caps) = re.captures(&resp) {
                if let Some(m) = caps.get(1) {
                    let reg_status: i32 = m.as_str().parse().unwrap_or(-1);
                    let status_str = match reg_status {
                        0 => "未注册",
                        1 => "已注册(本地)",
                        2 => "搜索中...",
                        3 => "注册被拒绝",
                        4 => "未知",
                        5 => "已注册(漫游)",
                        _ => "未知",
                    };
                    status.insert("network_reg".to_string(), status_str.to_string());
                }
            }
        }

        // 信号强度
        let (success, resp) = self.send_at_command("AT+CSQ", AT_TIMEOUT);
        if success {
            let re = Regex::new(r"\+CSQ:\s*(\d+),").unwrap();
            if let Some(caps) = re.captures(&resp) {
                if let Some(m) = caps.get(1) {
                    let rssi: i32 = m.as_str().parse().unwrap_or(99);
                    if rssi == 99 {
                        status.insert("signal".to_string(), "未知或不可检测".to_string());
                    } else {
                        let dbm = -113 + 2 * rssi;
                        status.insert("signal".to_string(), format!("RSSI={} ({}dBm)", rssi, dbm));
                    }
                }
            }
        }

        status
    }

    fn fota_upgrade(&mut self, url: &str, auto_reset: i32, timeout: i32) -> (bool, String) {
        if url.len() > 700 {
            return (false, "URL长度超过700字符限制".to_string());
        }

        // 重置状态
        {
            let mut state = self.fota_state.lock().unwrap();
            state.complete = false;
            state.result = -1;
        }
        *self.stop_monitor.lock().unwrap() = false;

        println!("\n{}", "=".repeat(50));
        log("🔄 开始FOTA升级");
        println!("{}", "=".repeat(50));

        // 1. 查询当前版本
        log("\n[步骤1] 查询当前固件版本...");
        let current_version = self.get_firmware_version();
        if !current_version.is_empty() {
            log(&format!("📌 当前版本: {}", current_version));
        }

        // 2. 检查网络状态
        log("\n[步骤2] 检查网络状态...");
        let status = self.check_network_status();
        let net_reg = status.get("network_reg").cloned().unwrap_or_default();
        if net_reg != "已注册(本地)" && net_reg != "已注册(漫游)" {
            return (false, format!("网络未注册: {}", net_reg));
        }
        log(&format!("✅ 网络已连接: {}", net_reg));
        if let Some(sig) = status.get("signal") {
            log(&format!("📶 信号强度: {}", sig));
        }

        // 3. 发送FOTA升级指令
        log("\n[步骤3] 发送FOTA升级指令...");
        log(&format!("📎 URL: {}", url));
        let mode_str = if auto_reset == 1 {
            "自动重启"
        } else {
            "手动重启"
        };
        log(&format!("📎 升级模式: {}", mode_str));
        log(&format!("📎 超时时间: {}秒", timeout));

        // AT+QFOTADL="URL",升级模式,超时时间
        let cmd = format!("AT+QFOTADL=\"{}\",{},{}", url, auto_reset, timeout);
        let (success, resp) = self.send_at_command(&cmd, Duration::from_secs(5));

        if !success {
            return (false, format!("指令发送失败: {}", resp));
        }

        log("✅ 指令发送成功，模组开始下载固件包...");
        log("\n[步骤4] 等待升级进度上报...");

        (true, "FOTA升级已启动".to_string())
    }

    fn wait_for_fota_complete(&self, max_wait: Duration) -> (bool, i32) {
        log(&format!("\n⏳ 等待升级完成（最长{:?}）...", max_wait));

        let start = Instant::now();
        while start.elapsed() < max_wait {
            let state = self.fota_state.lock().unwrap();
            if state.complete {
                return (state.result == 0, state.result);
            }
            drop(state);
            thread::sleep(Duration::from_millis(500));
        }

        (false, -1) // 超时
    }
}

fn list_serial_ports() {
    println!("\n📋 可用串口列表:");
    println!("{}", "-".repeat(50));

    match available_ports() {
        Ok(ports) => {
            if ports.is_empty() {
                println!("  未发现可用串口");
            } else {
                for port in ports {
                    println!("  {}", port.port_name);
                    match port.port_type {
                        serialport::SerialPortType::UsbPort(info) => {
                            println!("    制造商: {}", info.manufacturer.unwrap_or_default());
                        }
                        _ => {}
                    }
                }
            }
        }
        Err(e) => {
            println!("  获取串口列表失败: {}", e);
        }
    }
    println!();
}

fn run_basic_test(modem: &mut EC800KModem) -> bool {
    println!("\n{}", "=".repeat(50));
    println!("📡 EC800K/EG800K 基本测试");
    println!("{}", "=".repeat(50));

    // AT测试
    println!("\n[1/3] AT通信测试...");
    if modem.test_at() {
        println!("✅ AT通信正常");
    } else {
        println!("❌ AT通信失败");
        return false;
    }

    // 模块信息
    println!("\n[2/3] 获取模块信息...");
    let info = modem.get_module_info();
    for (key, value) in &info {
        println!("  {}: {}", key, value);
    }

    // 网络状态
    println!("\n[3/3] 检查网络状态...");
    let status = modem.check_network_status();
    for (key, value) in &status {
        println!("  {}: {}", key, value);
    }

    true
}

fn run_fota_test(modem: &mut EC800KModem, url: &str, auto_reset: i32, timeout: i32) -> bool {
    // 开始升级
    let (success, msg) = modem.fota_upgrade(url, auto_reset, timeout);
    if !success {
        log(&format!("❌ {}", msg));
        return false;
    }

    // 等待完成 (简化版，不启动后台监听线程)
    let (success, result_code) = modem.wait_for_fota_complete(Duration::from_secs(300));

    if success {
        log("\n[步骤5] 验证新版本...");
        thread::sleep(Duration::from_secs(5));
        let new_version = modem.get_firmware_version();
        if !new_version.is_empty() {
            log(&format!("📌 新版本: {}", new_version));
        }
        log("✅ FOTA升级成功!");
    } else if result_code == -1 {
        log("❌ 等待超时");
    } else {
        log(&format!("❌ 升级失败，错误码: {}", result_code));
    }

    success
}

fn print_error_codes() {
    println!("\n{}", "=".repeat(50));
    println!("📖 FOTA 错误码说明");
    println!("{}", "=".repeat(50));

    println!("\n【FOTA升级错误码】(+QIND: \"FOTA\",\"END\",<err>)");
    let dfota_errors = [
        (0, "升级成功"),
        (504, "升级失败"),
        (505, "包校验出错"),
        (506, "固件MD5检查错误"),
        (507, "包版本不匹配"),
        (552, "包项目名不匹配"),
        (553, "包基线名不匹配"),
    ];
    for (code, desc) in dfota_errors {
        println!("  {}: {}", code, desc);
    }

    println!("\n【+QIND URC上报说明】");
    println!("  +QIND: \"FOTA\",\"HTTPSTART\"     - 开始HTTP下载");
    println!("  +QIND: \"FOTA\",\"HTTPEND\",<err> - HTTP下载结束");
    println!("  +QIND: \"FOTA\",\"UPDATING\",<%>  - 升级进度(7%-96%)");
    println!("  +QIND: \"FOTA\",\"END\",<err>     - 升级结束(0=成功)");
}

fn print_usage() {
    println!("\n使用方法:");
    println!("  cargo run -- <串口> [命令] [参数...]");
    println!("\n命令:");
    println!("  test                   - 基本测试（默认）");
    println!("  info                   - 显示错误码说明");
    println!("  version                - 仅查询固件版本");
    println!("  fota URL [mode] [timeout]");
    println!("                         - FOTA升级");
    println!("                           mode: 0=手动重启, 1=自动重启");
    println!("\n示例:");
    println!("  cargo run -- /dev/ttyUSB0 test");
    println!("  cargo run -- COM3 fota \"http://server/fota.bin\" 0 50");
}

fn main() {
    println!("{}", "=".repeat(50));
    println!("🚀 EC800K/EG800K FOTA 测试工具 (Rust)");
    println!("   基于 Quectel DFOTA升级指导 V1.4");
    println!("{}", "=".repeat(50));

    list_serial_ports();

    let args: Vec<String> = env::args().collect();

    if args.len() < 2 {
        print_usage();
        return;
    }

    let port = &args[1];
    let command = args.get(2).map(|s| s.as_str()).unwrap_or("test");

    if command == "info" {
        print_error_codes();
        return;
    }

    let mut modem = EC800KModem::new(port, DEFAULT_BAUDRATE);

    match modem.connect() {
        Ok(_) => {}
        Err(e) => {
            println!("❌ {}", e);
            println!("\n💡 提示: 请检查串口连接和权限");
            return;
        }
    }

    match command {
        "test" => {
            run_basic_test(&mut modem);
        }
        "version" => {
            let version = modem.get_firmware_version();
            if !version.is_empty() {
                println!("\n📌 固件版本: {}", version);
            } else {
                println!("\n❌ 无法获取版本");
            }
        }
        "fota" => {
            if args.len() < 4 {
                println!("❌ 请提供FOTA包URL");
                println!("   用法: cargo run -- <串口> fota <URL> [mode] [timeout]");
            } else {
                let url = &args[3];
                let auto_reset = args.get(4).and_then(|s| s.parse().ok()).unwrap_or(0);
                let timeout = args.get(5).and_then(|s| s.parse().ok()).unwrap_or(50);
                run_fota_test(&mut modem, url, auto_reset, timeout);
            }
        }
        _ => {
            println!("❌ 未知命令: {}", command);
        }
    }

    modem.disconnect();
    println!("\n✨ 完成");
}
