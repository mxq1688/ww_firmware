// EC800K/EG800K FOTA 升级测试脚本 - Go版
// 基于 Quectel LTE Standard(A)系列 DFOTA 升级指导 V1.4
//
// 升级流程：
// 1. 查询当前版本 (AT+QGMR)
// 2. 发送升级指令 (AT+QFOTADL="URL",mode,timeout)
// 3. 监听进度上报 (+QIND: "FOTA","UPDATING",进度)
// 4. 等待升级完成 (+QIND: "FOTA","END",0)
//
// 依赖: go get go.bug.st/serial

package main

import (
	"fmt"
	"os"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"time"

	"go.bug.st/serial"
)

const (
	DefaultBaudRate = 115200
	ATTimeout       = 2 * time.Second
)

// 带时间戳的日志
func log(format string, args ...interface{}) {
	timestamp := time.Now().Format("15:04:05.000")
	msg := fmt.Sprintf(format, args...)
	fmt.Printf("[%s] %s\n", timestamp, msg)
}

// EC800KModem 模块控制结构
type EC800KModem struct {
	portPath         string
	baudRate         int
	port             serial.Port
	stopMonitor      bool
	monitorMutex     sync.Mutex
	fotaComplete     bool
	fotaResult       int
	progressCallback func(status string, value int)
}

// NewEC800KModem 创建新的模块实例
func NewEC800KModem(portPath string, baudRate int) *EC800KModem {
	return &EC800KModem{
		portPath:   portPath,
		baudRate:   baudRate,
		fotaResult: -1,
	}
}

// Connect 连接串口
func (m *EC800KModem) Connect() error {
	mode := &serial.Mode{
		BaudRate: m.baudRate,
		DataBits: 8,
		Parity:   serial.NoParity,
		StopBits: serial.OneStopBit,
	}

	port, err := serial.Open(m.portPath, mode)
	if err != nil {
		return fmt.Errorf("串口连接失败: %v", err)
	}

	m.port = port
	log("✅ 串口连接成功: %s @ %dbps", m.portPath, m.baudRate)
	return nil
}

// Disconnect 断开连接
func (m *EC800KModem) Disconnect() {
	m.stopMonitor = true
	if m.port != nil {
		m.port.Close()
		log("🔌 串口已断开")
	}
}

// SendATCommand 发送AT命令并获取响应
func (m *EC800KModem) SendATCommand(cmd string, timeout time.Duration) (bool, string) {
	log("📤 发送: %s", cmd)

	// 发送命令
	_, err := m.port.Write([]byte(cmd + "\r\n"))
	if err != nil {
		return false, fmt.Sprintf("发送失败: %v", err)
	}

	// 设置读取超时
	m.port.SetReadTimeout(timeout)

	// 读取响应
	response := ""
	buf := make([]byte, 256)
	startTime := time.Now()

	for time.Since(startTime) < timeout {
		n, err := m.port.Read(buf)
		if err != nil {
			break
		}
		if n > 0 {
			response += string(buf[:n])
			if strings.Contains(response, "OK") || strings.Contains(response, "ERROR") {
				break
			}
		}
	}

	response = strings.TrimSpace(response)
	if response != "" {
		log("📥 响应: %s", response)
	}

	success := strings.Contains(response, "OK")
	return success, response
}

// MonitorFOTAProgress 监听FOTA进度
func (m *EC800KModem) MonitorFOTAProgress() {
	m.port.SetReadTimeout(100 * time.Millisecond)
	buffer := ""

	updateRe := regexp.MustCompile(`\+QIND:\s*"FOTA"\s*,\s*"UPDATING"\s*,\s*(\d+)`)
	endRe := regexp.MustCompile(`\+QIND:\s*"FOTA"\s*,\s*"END"\s*,\s*(\d+)`)

	for !m.stopMonitor {
		buf := make([]byte, 256)
		n, _ := m.port.Read(buf)
		if n > 0 {
			buffer += string(buf[:n])

			// 按行处理
			for strings.Contains(buffer, "\n") {
				idx := strings.Index(buffer, "\n")
				line := strings.TrimSpace(buffer[:idx])
				buffer = buffer[idx+1:]

				if line == "" {
					continue
				}

				// 解析 +QIND: "FOTA","UPDATING",进度
				if matches := updateRe.FindStringSubmatch(line); len(matches) > 1 {
					progress, _ := strconv.Atoi(matches[1])
					log("📊 升级进度: %d%%", progress)
					if m.progressCallback != nil {
						m.progressCallback("UPDATING", progress)
					}
					continue
				}

				// 解析 +QIND: "FOTA","END",结果码
				if matches := endRe.FindStringSubmatch(line); len(matches) > 1 {
					result, _ := strconv.Atoi(matches[1])
					m.monitorMutex.Lock()
					m.fotaComplete = true
					m.fotaResult = result
					m.monitorMutex.Unlock()

					if result == 0 {
						log("✅ FOTA升级完成!")
					} else {
						log("❌ FOTA升级失败，错误码: %d", result)
					}
					if m.progressCallback != nil {
						m.progressCallback("END", result)
					}
					continue
				}

				// 其他 +QIND 消息
				if strings.Contains(line, "+QIND:") {
					log("📨 %s", line)
					continue
				}

				// 开机信息
				if line == "RDY" || line == "+CFUN: 1" ||
					strings.HasPrefix(line, "+CPIN:") ||
					strings.HasPrefix(line, "+QUSIM:") {
					log("📨 开机信息: %s", line)
				}
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
}

// TestAT 测试AT通信
func (m *EC800KModem) TestAT() bool {
	success, _ := m.SendATCommand("AT", ATTimeout)
	return success
}

// GetFirmwareVersion 获取固件版本 (使用AT+QGMR)
func (m *EC800KModem) GetFirmwareVersion() string {
	success, resp := m.SendATCommand("AT+QGMR", ATTimeout)
	if success {
		lines := strings.Split(resp, "\n")
		for _, line := range lines {
			line = strings.TrimSpace(line)
			// 版本格式: EG800KEULCR07A07M04_01.300.01.300
			if line != "" && !strings.HasPrefix(line, "AT") && line != "OK" {
				return line
			}
		}
	}
	return ""
}

// GetModuleInfo 获取模块信息
func (m *EC800KModem) GetModuleInfo() map[string]string {
	info := make(map[string]string)

	// 固件版本 (使用AT+QGMR)
	version := m.GetFirmwareVersion()
	if version != "" {
		info["firmware_version"] = version
		re := regexp.MustCompile(`(\d+\.\d+\.\d+\.\d+)$`)
		if match := re.FindString(version); match != "" {
			info["version_number"] = match
		}
	}

	// IMEI
	if success, resp := m.SendATCommand("AT+GSN", ATTimeout); success {
		re := regexp.MustCompile(`\d{15}`)
		if match := re.FindString(resp); match != "" {
			info["imei"] = match
		}
	}

	// SIM卡状态
	if success, resp := m.SendATCommand("AT+CPIN?", ATTimeout); success {
		if strings.Contains(resp, "READY") {
			info["sim_status"] = "已就绪"
		} else {
			info["sim_status"] = resp
		}
	}

	return info
}

// CheckNetworkStatus 检查网络状态
func (m *EC800KModem) CheckNetworkStatus() map[string]string {
	status := make(map[string]string)

	// 网络注册状态
	if success, resp := m.SendATCommand("AT+CREG?", ATTimeout); success {
		re := regexp.MustCompile(`\+CREG:\s*\d+,(\d+)`)
		if matches := re.FindStringSubmatch(resp); len(matches) > 1 {
			regStatus, _ := strconv.Atoi(matches[1])
			statusMap := map[int]string{
				0: "未注册", 1: "已注册(本地)", 2: "搜索中...",
				3: "注册被拒绝", 4: "未知", 5: "已注册(漫游)",
			}
			if s, ok := statusMap[regStatus]; ok {
				status["network_reg"] = s
			} else {
				status["network_reg"] = fmt.Sprintf("未知(%d)", regStatus)
			}
		}
	}

	// 信号强度
	if success, resp := m.SendATCommand("AT+CSQ", ATTimeout); success {
		re := regexp.MustCompile(`\+CSQ:\s*(\d+),`)
		if matches := re.FindStringSubmatch(resp); len(matches) > 1 {
			rssi, _ := strconv.Atoi(matches[1])
			if rssi == 99 {
				status["signal"] = "未知或不可检测"
			} else {
				dbm := -113 + 2*rssi
				status["signal"] = fmt.Sprintf("RSSI=%d (%ddBm)", rssi, dbm)
			}
		}
	}

	return status
}

// FOTAUpgrade 执行FOTA升级
func (m *EC800KModem) FOTAUpgrade(url string, autoReset int, timeout int, callback func(string, int)) (bool, string) {
	if len(url) > 700 {
		return false, "URL长度超过700字符限制"
	}

	m.progressCallback = callback
	m.fotaComplete = false
	m.fotaResult = -1

	fmt.Println("\n" + strings.Repeat("=", 50))
	log("🔄 开始FOTA升级")
	fmt.Println(strings.Repeat("=", 50))

	// 1. 查询当前版本
	log("\n[步骤1] 查询当前固件版本...")
	currentVersion := m.GetFirmwareVersion()
	if currentVersion != "" {
		log("📌 当前版本: %s", currentVersion)
	}

	// 2. 检查网络状态
	log("\n[步骤2] 检查网络状态...")
	status := m.CheckNetworkStatus()
	netReg := status["network_reg"]
	if netReg != "已注册(本地)" && netReg != "已注册(漫游)" {
		return false, fmt.Sprintf("网络未注册: %s", netReg)
	}
	log("✅ 网络已连接: %s", netReg)
	if sig, ok := status["signal"]; ok {
		log("📶 信号强度: %s", sig)
	}

	// 3. 发送FOTA升级指令
	log("\n[步骤3] 发送FOTA升级指令...")
	log("📎 URL: %s", url)
	modeStr := "手动重启"
	if autoReset == 1 {
		modeStr = "自动重启"
	}
	log("📎 升级模式: %s", modeStr)
	log("📎 超时时间: %d秒", timeout)

	// AT+QFOTADL="URL",升级模式,超时时间
	cmd := fmt.Sprintf(`AT+QFOTADL="%s",%d,%d`, url, autoReset, timeout)

	// 启动进度监听
	m.stopMonitor = false
	go m.MonitorFOTAProgress()

	success, resp := m.SendATCommand(cmd, 5*time.Second)

	if !success {
		m.stopMonitor = true
		return false, fmt.Sprintf("指令发送失败: %s", resp)
	}

	log("✅ 指令发送成功，模组开始下载固件包...")
	log("\n[步骤4] 等待升级进度上报...")

	return true, "FOTA升级已启动"
}

// WaitForFOTAComplete 等待FOTA升级完成
func (m *EC800KModem) WaitForFOTAComplete(maxWait time.Duration) (bool, int) {
	log("\n⏳ 等待升级完成（最长%v）...", maxWait)

	startTime := time.Now()
	for time.Since(startTime) < maxWait {
		m.monitorMutex.Lock()
		complete := m.fotaComplete
		result := m.fotaResult
		m.monitorMutex.Unlock()

		if complete {
			m.stopMonitor = true
			return result == 0, result
		}
		time.Sleep(500 * time.Millisecond)
	}

	m.stopMonitor = true
	return false, -1 // 超时
}

// 列出可用串口
func listSerialPorts() {
	ports, err := serial.GetPortsList()
	fmt.Println("\n📋 可用串口列表:")
	fmt.Println(strings.Repeat("-", 50))

	if err != nil {
		fmt.Printf("  获取串口列表失败: %v\n", err)
		return
	}

	if len(ports) == 0 {
		fmt.Println("  未发现可用串口")
	} else {
		for _, port := range ports {
			fmt.Printf("  %s\n", port)
		}
	}
	fmt.Println()
}

// 运行基本测试
func runBasicTest(modem *EC800KModem) bool {
	fmt.Println("\n" + strings.Repeat("=", 50))
	fmt.Println("📡 EC800K/EG800K 基本测试")
	fmt.Println(strings.Repeat("=", 50))

	// AT测试
	fmt.Println("\n[1/3] AT通信测试...")
	if modem.TestAT() {
		fmt.Println("✅ AT通信正常")
	} else {
		fmt.Println("❌ AT通信失败")
		return false
	}

	// 模块信息
	fmt.Println("\n[2/3] 获取模块信息...")
	info := modem.GetModuleInfo()
	for key, value := range info {
		fmt.Printf("  %s: %s\n", key, value)
	}

	// 网络状态
	fmt.Println("\n[3/3] 检查网络状态...")
	status := modem.CheckNetworkStatus()
	for key, value := range status {
		fmt.Printf("  %s: %s\n", key, value)
	}

	return true
}

// 运行FOTA升级测试
func runFOTATest(modem *EC800KModem, url string, autoReset, timeout int) bool {
	// 进度回调
	onProgress := func(status string, value int) {
		if status == "UPDATING" {
			barLen := 30
			filled := barLen * value / 100
			bar := strings.Repeat("█", filled) + strings.Repeat("░", barLen-filled)
			fmt.Printf("\r  [%s] %d%%", bar, value)
		} else if status == "END" {
			fmt.Println()
		}
	}

	// 开始升级
	success, msg := modem.FOTAUpgrade(url, autoReset, timeout, onProgress)
	if !success {
		log("❌ %s", msg)
		return false
	}

	// 等待完成
	success, resultCode := modem.WaitForFOTAComplete(5 * time.Minute)

	if success {
		log("\n[步骤5] 验证新版本...")
		time.Sleep(5 * time.Second)
		newVersion := modem.GetFirmwareVersion()
		if newVersion != "" {
			log("📌 新版本: %s", newVersion)
		}
		log("✅ FOTA升级成功!")
	} else {
		if resultCode == -1 {
			log("❌ 等待超时")
		} else {
			log("❌ 升级失败，错误码: %d", resultCode)
		}
	}

	return success
}

// 打印错误码
func printErrorCodes() {
	fmt.Println("\n" + strings.Repeat("=", 50))
	fmt.Println("📖 FOTA 错误码说明")
	fmt.Println(strings.Repeat("=", 50))

	fmt.Println("\n【FOTA升级错误码】(+QIND: \"FOTA\",\"END\",<err>)")
	dfotaErrors := map[int]string{
		0: "升级成功", 504: "升级失败", 505: "包校验出错",
		506: "固件MD5检查错误", 507: "包版本不匹配",
		552: "包项目名不匹配", 553: "包基线名不匹配",
	}
	for code, desc := range dfotaErrors {
		fmt.Printf("  %d: %s\n", code, desc)
	}

	fmt.Println("\n【+QIND URC上报说明】")
	fmt.Println("  +QIND: \"FOTA\",\"HTTPSTART\"     - 开始HTTP下载")
	fmt.Println("  +QIND: \"FOTA\",\"HTTPEND\",<err> - HTTP下载结束")
	fmt.Println("  +QIND: \"FOTA\",\"UPDATING\",<%>  - 升级进度(7%-96%)")
	fmt.Println("  +QIND: \"FOTA\",\"END\",<err>     - 升级结束(0=成功)")
}

func printUsage() {
	fmt.Println("\n使用方法:")
	fmt.Println("  go run main.go <串口> [命令] [参数...]")
	fmt.Println("\n命令:")
	fmt.Println("  test                   - 基本测试（默认）")
	fmt.Println("  info                   - 显示错误码说明")
	fmt.Println("  version                - 仅查询固件版本")
	fmt.Println("  fota URL [mode] [timeout]")
	fmt.Println("                         - FOTA升级")
	fmt.Println("                           mode: 0=手动重启, 1=自动重启")
	fmt.Println("\n示例:")
	fmt.Println("  go run main.go /dev/ttyUSB0 test")
	fmt.Println("  go run main.go COM3 fota \"http://server/fota.bin\" 0 50")
}

func main() {
	fmt.Println(strings.Repeat("=", 50))
	fmt.Println("🚀 EC800K/EG800K FOTA 测试工具 (Go)")
	fmt.Println("   基于 Quectel DFOTA升级指导 V1.4")
	fmt.Println(strings.Repeat("=", 50))

	listSerialPorts()

	if len(os.Args) < 2 {
		printUsage()
		return
	}

	port := os.Args[1]
	command := "test"
	if len(os.Args) > 2 {
		command = os.Args[2]
	}

	if command == "info" {
		printErrorCodes()
		return
	}

	modem := NewEC800KModem(port, DefaultBaudRate)

	if err := modem.Connect(); err != nil {
		fmt.Printf("❌ %v\n", err)
		fmt.Println("\n💡 提示: 请检查串口连接和权限")
		return
	}
	defer modem.Disconnect()

	switch command {
	case "test":
		runBasicTest(modem)
	case "version":
		version := modem.GetFirmwareVersion()
		if version != "" {
			fmt.Printf("\n📌 固件版本: %s\n", version)
		} else {
			fmt.Println("\n❌ 无法获取版本")
		}
	case "fota":
		if len(os.Args) < 4 {
			fmt.Println("❌ 请提供FOTA包URL")
			fmt.Println("   用法: go run main.go <串口> fota <URL> [mode] [timeout]")
		} else {
			url := os.Args[3]
			autoReset := 0
			timeout := 50
			if len(os.Args) > 4 {
				autoReset, _ = strconv.Atoi(os.Args[4])
			}
			if len(os.Args) > 5 {
				timeout, _ = strconv.Atoi(os.Args[5])
			}
			runFOTATest(modem, url, autoReset, timeout)
		}
	default:
		fmt.Printf("❌ 未知命令: %s\n", command)
	}

	fmt.Println("\n✨ 完成")
}
