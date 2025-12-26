/**
 * EC800K/EG800K FOTA 升级测试脚本 - C#版
 * 基于 Quectel LTE Standard(A)系列 DFOTA 升级指导 V1.4
 *
 * 升级流程：
 * 1. 查询当前版本 (AT+QGMR)
 * 2. 发送升级指令 (AT+QFOTADL="URL",mode,timeout)
 * 3. 监听进度上报 (+QIND: "FOTA","UPDATING",进度)
 * 4. 等待升级完成 (+QIND: "FOTA","END",0)
 *
 * 运行: dotnet run -- <串口> [命令]
 */

using System;
using System.Collections.Generic;
using System.IO.Ports;
using System.Text.RegularExpressions;
using System.Threading;

namespace EC800KDfotaTest
{
    class EC800KModem
    {
        private const int DEFAULT_BAUDRATE = 115200;
        private const int AT_TIMEOUT = 2000;

        private SerialPort? _port;
        private readonly string _portPath;
        private readonly int _baudRate;
        private string _responseBuffer = "";
        private readonly object _lock = new();
        
        private volatile bool _stopMonitor = false;
        private volatile bool _fotaComplete = false;
        private volatile int _fotaResult = -1;
        private Action<string, int>? _progressCallback;

        public EC800KModem(string portPath, int baudRate = DEFAULT_BAUDRATE)
        {
            _portPath = portPath;
            _baudRate = baudRate;
        }

        private static void Log(string msg)
        {
            var timestamp = DateTime.Now.ToString("HH:mm:ss.fff");
            Console.WriteLine($"[{timestamp}] {msg}");
        }

        public bool Connect()
        {
            try
            {
                _port = new SerialPort(_portPath, _baudRate, Parity.None, 8, StopBits.One)
                {
                    ReadTimeout = AT_TIMEOUT,
                    WriteTimeout = 1000,
                    Handshake = Handshake.None,
                    DtrEnable = true,
                    RtsEnable = true
                };

                _port.DataReceived += OnDataReceived;
                _port.Open();
                Log($"✅ 串口连接成功: {_portPath} @ {_baudRate}bps");
                return true;
            }
            catch (Exception ex)
            {
                Log($"❌ 串口连接失败: {ex.Message}");
                return false;
            }
        }

        private void OnDataReceived(object sender, SerialDataReceivedEventArgs e)
        {
            try
            {
                var data = _port?.ReadExisting() ?? "";
                
                lock (_lock)
                {
                    _responseBuffer += data;
                }

                // 解析 +QIND URC
                foreach (var line in data.Split('\n'))
                {
                    var trimmed = line.Trim();
                    if (string.IsNullOrEmpty(trimmed)) continue;

                    // 解析 +QIND: "FOTA","UPDATING",进度
                    var updateMatch = Regex.Match(trimmed, @"\+QIND:\s*""FOTA""\s*,\s*""UPDATING""\s*,\s*(\d+)");
                    if (updateMatch.Success)
                    {
                        var progress = int.Parse(updateMatch.Groups[1].Value);
                        Log($"📊 升级进度: {progress}%");
                        _progressCallback?.Invoke("UPDATING", progress);
                        continue;
                    }

                    // 解析 +QIND: "FOTA","END",结果码
                    var endMatch = Regex.Match(trimmed, @"\+QIND:\s*""FOTA""\s*,\s*""END""\s*,\s*(\d+)");
                    if (endMatch.Success)
                    {
                        var result = int.Parse(endMatch.Groups[1].Value);
                        _fotaComplete = true;
                        _fotaResult = result;
                        if (result == 0)
                            Log("✅ FOTA升级完成!");
                        else
                            Log($"❌ FOTA升级失败，错误码: {result}");
                        _progressCallback?.Invoke("END", result);
                        continue;
                    }

                    // 其他 +QIND 消息
                    if (trimmed.Contains("+QIND:"))
                    {
                        Log($"📨 {trimmed}");
                        continue;
                    }

                    // 开机信息
                    if (trimmed == "RDY" || trimmed == "+CFUN: 1" ||
                        trimmed.StartsWith("+CPIN:") || trimmed.StartsWith("+QUSIM:"))
                    {
                        Log($"📨 开机信息: {trimmed}");
                    }
                }
            }
            catch { }
        }

        public void Disconnect()
        {
            _stopMonitor = true;
            if (_port != null && _port.IsOpen)
            {
                _port.Close();
                _port.Dispose();
                Log("🔌 串口已断开");
            }
        }

        public (bool Success, string Response) SendATCommand(string cmd, int timeout = AT_TIMEOUT)
        {
            if (_port == null || !_port.IsOpen)
            {
                return (false, "串口未连接");
            }

            Log($"📤 发送: {cmd}");

            lock (_lock)
            {
                _responseBuffer = "";
            }

            try
            {
                _port.Write($"{cmd}\r\n");
            }
            catch (Exception ex)
            {
                return (false, $"发送失败: {ex.Message}");
            }

            // 等待响应
            var startTime = DateTime.Now;
            while ((DateTime.Now - startTime).TotalMilliseconds < timeout)
            {
                lock (_lock)
                {
                    if (_responseBuffer.Contains("OK") || _responseBuffer.Contains("ERROR"))
                    {
                        break;
                    }
                }
                Thread.Sleep(50);
            }

            string response;
            lock (_lock)
            {
                response = _responseBuffer.Trim();
            }

            if (!string.IsNullOrEmpty(response))
                Log($"📥 响应: {response}");

            bool success = response.Contains("OK");
            return (success, response);
        }

        public bool TestAT()
        {
            var (success, _) = SendATCommand("AT");
            return success;
        }

        public string GetFirmwareVersion()
        {
            // 使用 AT+QGMR 查询版本
            var (success, resp) = SendATCommand("AT+QGMR");
            if (success)
            {
                foreach (var line in resp.Split('\n'))
                {
                    var trimmed = line.Trim();
                    // 版本格式: EG800KEULCR07A07M04_01.300.01.300
                    if (!string.IsNullOrEmpty(trimmed) && 
                        !trimmed.StartsWith("AT") && 
                        trimmed != "OK")
                    {
                        return trimmed;
                    }
                }
            }
            return "";
        }

        public Dictionary<string, string> GetModuleInfo()
        {
            var info = new Dictionary<string, string>();

            // 固件版本 (使用AT+QGMR)
            var version = GetFirmwareVersion();
            if (!string.IsNullOrEmpty(version))
            {
                info["firmware_version"] = version;
                var match = Regex.Match(version, @"(\d+\.\d+\.\d+\.\d+)$");
                if (match.Success) info["version_number"] = match.Groups[1].Value;
            }

            // IMEI
            var (success, resp) = SendATCommand("AT+GSN");
            if (success)
            {
                var match = Regex.Match(resp, @"\d{15}");
                if (match.Success) info["imei"] = match.Value;
            }

            // SIM卡状态
            (success, resp) = SendATCommand("AT+CPIN?");
            if (success)
            {
                info["sim_status"] = resp.Contains("READY") ? "已就绪" : resp;
            }

            return info;
        }

        public Dictionary<string, string> CheckNetworkStatus()
        {
            var status = new Dictionary<string, string>();

            // 网络注册状态
            var (success, resp) = SendATCommand("AT+CREG?");
            if (success)
            {
                var match = Regex.Match(resp, @"\+CREG:\s*\d+,(\d+)");
                if (match.Success)
                {
                    int regStatus = int.Parse(match.Groups[1].Value);
                    var statusMap = new Dictionary<int, string>
                    {
                        {0, "未注册"}, {1, "已注册(本地)"}, {2, "搜索中..."},
                        {3, "注册被拒绝"}, {4, "未知"}, {5, "已注册(漫游)"}
                    };
                    status["network_reg"] = statusMap.GetValueOrDefault(regStatus, $"未知({regStatus})");
                }
            }

            // 信号强度
            (success, resp) = SendATCommand("AT+CSQ");
            if (success)
            {
                var match = Regex.Match(resp, @"\+CSQ:\s*(\d+),");
                if (match.Success)
                {
                    int rssi = int.Parse(match.Groups[1].Value);
                    if (rssi == 99)
                    {
                        status["signal"] = "未知或不可检测";
                    }
                    else
                    {
                        int dbm = -113 + 2 * rssi;
                        status["signal"] = $"RSSI={rssi} ({dbm}dBm)";
                    }
                }
            }

            return status;
        }

        public (bool Success, string Response) FOTAUpgrade(string url, int autoReset = 0, int timeout = 50,
            Action<string, int>? progressCallback = null)
        {
            if (url.Length > 700)
            {
                return (false, "URL长度超过700字符限制");
            }

            _progressCallback = progressCallback;
            _fotaComplete = false;
            _fotaResult = -1;
            _stopMonitor = false;

            Console.WriteLine("\n" + new string('=', 50));
            Log("🔄 开始FOTA升级");
            Console.WriteLine(new string('=', 50));

            // 1. 查询当前版本
            Log("\n[步骤1] 查询当前固件版本...");
            var currentVersion = GetFirmwareVersion();
            if (!string.IsNullOrEmpty(currentVersion))
            {
                Log($"📌 当前版本: {currentVersion}");
            }

            // 2. 检查网络状态
            Log("\n[步骤2] 检查网络状态...");
            var status = CheckNetworkStatus();
            var netReg = status.GetValueOrDefault("network_reg", "");
            if (netReg != "已注册(本地)" && netReg != "已注册(漫游)")
            {
                return (false, $"网络未注册: {netReg}");
            }
            Log($"✅ 网络已连接: {netReg}");
            if (status.TryGetValue("signal", out var sig))
            {
                Log($"📶 信号强度: {sig}");
            }

            // 3. 发送FOTA升级指令
            Log("\n[步骤3] 发送FOTA升级指令...");
            Log($"📎 URL: {url}");
            Log($"📎 升级模式: {(autoReset == 1 ? "自动重启" : "手动重启")}");
            Log($"📎 超时时间: {timeout}秒");

            // AT+QFOTADL="URL",升级模式,超时时间
            var cmd = $"AT+QFOTADL=\"{url}\",{autoReset},{timeout}";
            var (success, resp) = SendATCommand(cmd, 5000);

            if (!success)
            {
                return (false, $"指令发送失败: {resp}");
            }

            Log("✅ 指令发送成功，模组开始下载固件包...");
            Log("\n[步骤4] 等待升级进度上报...");

            return (true, "FOTA升级已启动");
        }

        public (bool Success, int ResultCode) WaitForFOTAComplete(int maxWaitMs = 300000)
        {
            Log($"\n⏳ 等待升级完成（最长{maxWaitMs / 1000}秒）...");

            var startTime = DateTime.Now;
            while ((DateTime.Now - startTime).TotalMilliseconds < maxWaitMs)
            {
                if (_fotaComplete)
                {
                    return (_fotaResult == 0, _fotaResult);
                }
                Thread.Sleep(500);
            }

            return (false, -1); // 超时
        }
    }

    class Program
    {
        static void ListSerialPorts()
        {
            Console.WriteLine("\n📋 可用串口列表:");
            Console.WriteLine(new string('-', 50));

            string[] ports = SerialPort.GetPortNames();
            if (ports.Length == 0)
            {
                Console.WriteLine("  未发现可用串口");
            }
            else
            {
                foreach (string port in ports)
                {
                    Console.WriteLine($"  {port}");
                }
            }
            Console.WriteLine();
        }

        static bool RunBasicTest(EC800KModem modem)
        {
            Console.WriteLine("\n" + new string('=', 50));
            Console.WriteLine("📡 EC800K/EG800K 基本测试");
            Console.WriteLine(new string('=', 50));

            // AT测试
            Console.WriteLine("\n[1/3] AT通信测试...");
            if (modem.TestAT())
            {
                Console.WriteLine("✅ AT通信正常");
            }
            else
            {
                Console.WriteLine("❌ AT通信失败");
                return false;
            }

            // 模块信息
            Console.WriteLine("\n[2/3] 获取模块信息...");
            var info = modem.GetModuleInfo();
            foreach (var kvp in info)
            {
                Console.WriteLine($"  {kvp.Key}: {kvp.Value}");
            }

            // 网络状态
            Console.WriteLine("\n[3/3] 检查网络状态...");
            var status = modem.CheckNetworkStatus();
            foreach (var kvp in status)
            {
                Console.WriteLine($"  {kvp.Key}: {kvp.Value}");
            }

            return true;
        }

        static bool RunFOTATest(EC800KModem modem, string url, int autoReset = 0, int timeout = 50)
        {
            // 进度回调
            void OnProgress(string status, int value)
            {
                if (status == "UPDATING")
                {
                    int barLen = 30;
                    int filled = barLen * value / 100;
                    string bar = new string('█', filled) + new string('░', barLen - filled);
                    Console.Write($"\r  [{bar}] {value}%");
                }
                else if (status == "END")
                {
                    Console.WriteLine();
                }
            }

            // 开始升级
            var (success, msg) = modem.FOTAUpgrade(url, autoReset, timeout, OnProgress);
            if (!success)
            {
                Console.WriteLine($"❌ {msg}");
                return false;
            }

            // 等待完成
            var (fotaSuccess, resultCode) = modem.WaitForFOTAComplete(300000);

            if (fotaSuccess)
            {
                Console.WriteLine("\n[步骤5] 验证新版本...");
                Thread.Sleep(5000);
                var newVersion = modem.GetFirmwareVersion();
                if (!string.IsNullOrEmpty(newVersion))
                {
                    Console.WriteLine($"📌 新版本: {newVersion}");
                }
                Console.WriteLine("✅ FOTA升级成功!");
            }
            else
            {
                if (resultCode == -1)
                    Console.WriteLine("❌ 等待超时");
                else
                    Console.WriteLine($"❌ 升级失败，错误码: {resultCode}");
            }

            return fotaSuccess;
        }

        static void PrintErrorCodes()
        {
            Console.WriteLine("\n" + new string('=', 50));
            Console.WriteLine("📖 FOTA 错误码说明");
            Console.WriteLine(new string('=', 50));

            Console.WriteLine("\n【FOTA升级错误码】(+QIND: \"FOTA\",\"END\",<err>)");
            var dfotaErrors = new Dictionary<int, string>
            {
                {0, "升级成功"}, {504, "升级失败"}, {505, "包校验出错"},
                {506, "固件MD5检查错误"}, {507, "包版本不匹配"},
                {552, "包项目名不匹配"}, {553, "包基线名不匹配"}
            };
            foreach (var kvp in dfotaErrors)
            {
                Console.WriteLine($"  {kvp.Key}: {kvp.Value}");
            }

            Console.WriteLine("\n【+QIND URC上报说明】");
            Console.WriteLine("  +QIND: \"FOTA\",\"HTTPSTART\"     - 开始HTTP下载");
            Console.WriteLine("  +QIND: \"FOTA\",\"HTTPEND\",<err> - HTTP下载结束");
            Console.WriteLine("  +QIND: \"FOTA\",\"UPDATING\",<%>  - 升级进度(7%-96%)");
            Console.WriteLine("  +QIND: \"FOTA\",\"END\",<err>     - 升级结束(0=成功)");
        }

        static void PrintUsage()
        {
            Console.WriteLine("\n使用方法:");
            Console.WriteLine("  dotnet run -- <串口> [命令] [参数...]");
            Console.WriteLine("\n命令:");
            Console.WriteLine("  test                   - 基本测试（默认）");
            Console.WriteLine("  info                   - 显示错误码说明");
            Console.WriteLine("  version                - 仅查询固件版本");
            Console.WriteLine("  fota URL [mode] [timeout]");
            Console.WriteLine("                         - FOTA升级");
            Console.WriteLine("                           mode: 0=手动重启, 1=自动重启");
            Console.WriteLine("\n示例:");
            Console.WriteLine("  dotnet run -- COM3 test");
            Console.WriteLine("  dotnet run -- /dev/ttyUSB0 fota \"http://server/fota.bin\" 0 50");
        }

        static void Main(string[] args)
        {
            Console.WriteLine(new string('=', 50));
            Console.WriteLine("🚀 EC800K/EG800K FOTA 测试工具 (C#)");
            Console.WriteLine("   基于 Quectel DFOTA升级指导 V1.4");
            Console.WriteLine(new string('=', 50));

            ListSerialPorts();

            if (args.Length < 1)
            {
                PrintUsage();
                return;
            }

            string port = args[0];
            string command = args.Length > 1 ? args[1] : "test";

            if (command == "info")
            {
                PrintErrorCodes();
                return;
            }

            var modem = new EC800KModem(port);

            if (!modem.Connect())
            {
                Console.WriteLine("\n💡 提示: 请检查串口连接和权限");
                return;
            }

            try
            {
                switch (command)
                {
                    case "test":
                        RunBasicTest(modem);
                        break;
                    case "version":
                        var version = modem.GetFirmwareVersion();
                        if (!string.IsNullOrEmpty(version))
                            Console.WriteLine($"\n📌 固件版本: {version}");
                        else
                            Console.WriteLine("\n❌ 无法获取版本");
                        break;
                    case "fota":
                        if (args.Length < 3)
                        {
                            Console.WriteLine("❌ 请提供FOTA包URL");
                            Console.WriteLine("   用法: dotnet run -- <串口> fota <URL> [mode] [timeout]");
                        }
                        else
                        {
                            var url = args[2];
                            int autoReset = args.Length > 3 ? int.Parse(args[3]) : 0;
                            int timeout = args.Length > 4 ? int.Parse(args[4]) : 50;
                            RunFOTATest(modem, url, autoReset, timeout);
                        }
                        break;
                    default:
                        Console.WriteLine($"❌ 未知命令: {command}");
                        break;
                }
            }
            finally
            {
                modem.Disconnect();
            }

            Console.WriteLine("\n✨ 完成");
        }
    }
}
