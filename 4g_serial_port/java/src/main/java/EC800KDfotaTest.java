/**
 * EC800K/EG800K FOTA 升级测试脚本 - Java版
 * 基于 Quectel LTE Standard(A)系列 DFOTA 升级指导 V1.4
 *
 * 升级流程：
 * 1. 查询当前版本 (AT+QGMR)
 * 2. 发送升级指令 (AT+QFOTADL="URL",mode,timeout)
 * 3. 监听进度上报 (+QIND: "FOTA","UPDATING",进度)
 * 4. 等待升级完成 (+QIND: "FOTA","END",0)
 *
 * 依赖: jSerialComm
 * 编译运行: 
 *   cd java && mvn compile exec:java -Dexec.args="/dev/ttyUSB0 test"
 */

import com.fazecast.jSerialComm.SerialPort;
import com.fazecast.jSerialComm.SerialPortDataListener;
import com.fazecast.jSerialComm.SerialPortEvent;
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.*;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.BiConsumer;
import java.util.regex.*;

public class EC800KDfotaTest {

    private static final int DEFAULT_BAUDRATE = 115200;
    private static final int AT_TIMEOUT = 2000;
    private static final DateTimeFormatter TIME_FORMAT = DateTimeFormatter.ofPattern("HH:mm:ss.SSS");

    private SerialPort port;
    private final String portPath;
    private final int baudRate;
    private StringBuilder responseBuffer = new StringBuilder();
    private final Object lock = new Object();
    
    private volatile boolean stopMonitor = false;
    private AtomicBoolean fotaComplete = new AtomicBoolean(false);
    private AtomicInteger fotaResult = new AtomicInteger(-1);
    private BiConsumer<String, Integer> progressCallback;

    public EC800KDfotaTest(String portPath, int baudRate) {
        this.portPath = portPath;
        this.baudRate = baudRate;
    }

    public EC800KDfotaTest(String portPath) {
        this(portPath, DEFAULT_BAUDRATE);
    }

    private static void log(String msg) {
        String timestamp = LocalTime.now().format(TIME_FORMAT);
        System.out.printf("[%s] %s%n", timestamp, msg);
    }

    public boolean connect() {
        port = SerialPort.getCommPort(portPath);
        port.setBaudRate(baudRate);
        port.setNumDataBits(8);
        port.setNumStopBits(SerialPort.ONE_STOP_BIT);
        port.setParity(SerialPort.NO_PARITY);
        port.setComPortTimeouts(SerialPort.TIMEOUT_READ_SEMI_BLOCKING, AT_TIMEOUT, 1000);

        if (port.openPort()) {
            // 添加数据监听器
            port.addDataListener(new SerialPortDataListener() {
                @Override
                public int getListeningEvents() {
                    return SerialPort.LISTENING_EVENT_DATA_RECEIVED;
                }

                @Override
                public void serialEvent(SerialPortEvent event) {
                    if (event.getEventType() != SerialPort.LISTENING_EVENT_DATA_RECEIVED) return;
                    
                    byte[] data = event.getReceivedData();
                    String str = new String(data);
                    
                    synchronized (lock) {
                        responseBuffer.append(str);
                    }

                    // 解析 +QIND URC
                    for (String line : str.split("\n")) {
                        String trimmed = line.trim();
                        if (trimmed.isEmpty()) continue;

                        // 解析 +QIND: "FOTA","UPDATING",进度
                        Matcher updateMatch = Pattern.compile("\\+QIND:\\s*\"FOTA\"\\s*,\\s*\"UPDATING\"\\s*,\\s*(\\d+)")
                            .matcher(trimmed);
                        if (updateMatch.find()) {
                            int progress = Integer.parseInt(updateMatch.group(1));
                            log(String.format("📊 升级进度: %d%%", progress));
                            if (progressCallback != null) {
                                progressCallback.accept("UPDATING", progress);
                            }
                            continue;
                        }

                        // 解析 +QIND: "FOTA","END",结果码
                        Matcher endMatch = Pattern.compile("\\+QIND:\\s*\"FOTA\"\\s*,\\s*\"END\"\\s*,\\s*(\\d+)")
                            .matcher(trimmed);
                        if (endMatch.find()) {
                            int result = Integer.parseInt(endMatch.group(1));
                            fotaComplete.set(true);
                            fotaResult.set(result);
                            if (result == 0) {
                                log("✅ FOTA升级完成!");
                            } else {
                                log(String.format("❌ FOTA升级失败，错误码: %d", result));
                            }
                            if (progressCallback != null) {
                                progressCallback.accept("END", result);
                            }
                            continue;
                        }

                        // 其他 +QIND 消息
                        if (trimmed.contains("+QIND:")) {
                            log(String.format("📨 %s", trimmed));
                            continue;
                        }

                        // 开机信息
                        if (trimmed.equals("RDY") || trimmed.equals("+CFUN: 1") ||
                            trimmed.startsWith("+CPIN:") || trimmed.startsWith("+QUSIM:")) {
                            log(String.format("📨 开机信息: %s", trimmed));
                        }
                    }
                }
            });

            log(String.format("✅ 串口连接成功: %s @ %dbps", portPath, baudRate));
            return true;
        } else {
            log(String.format("❌ 串口连接失败: %s", portPath));
            return false;
        }
    }

    public void disconnect() {
        stopMonitor = true;
        if (port != null && port.isOpen()) {
            port.closePort();
            log("🔌 串口已断开");
        }
    }

    public static class ATResponse {
        public boolean success;
        public String response;

        public ATResponse(boolean success, String response) {
            this.success = success;
            this.response = response;
        }
    }

    public ATResponse sendATCommand(String cmd, int timeout) {
        if (port == null || !port.isOpen()) {
            return new ATResponse(false, "串口未连接");
        }

        log(String.format("📤 发送: %s", cmd));

        synchronized (lock) {
            responseBuffer.setLength(0);
        }

        // 发送命令
        String fullCmd = cmd + "\r\n";
        port.writeBytes(fullCmd.getBytes(), fullCmd.length());

        // 读取响应
        long startTime = System.currentTimeMillis();
        while (System.currentTimeMillis() - startTime < timeout) {
            synchronized (lock) {
                String resp = responseBuffer.toString();
                if (resp.contains("OK") || resp.contains("ERROR")) {
                    String trimmed = resp.trim();
                    if (!trimmed.isEmpty()) {
                        log(String.format("📥 响应: %s", trimmed));
                    }
                    return new ATResponse(resp.contains("OK"), trimmed);
                }
            }
            try {
                Thread.sleep(50);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }

        String resp;
        synchronized (lock) {
            resp = responseBuffer.toString().trim();
        }
        return new ATResponse(false, resp.isEmpty() ? "超时" : resp);
    }

    public ATResponse sendATCommand(String cmd) {
        return sendATCommand(cmd, AT_TIMEOUT);
    }

    public boolean testAT() {
        return sendATCommand("AT").success;
    }

    public String getFirmwareVersion() {
        // 使用 AT+QGMR 查询版本
        ATResponse resp = sendATCommand("AT+QGMR");
        if (resp.success) {
            for (String line : resp.response.split("\n")) {
                String trimmed = line.trim();
                // 版本格式: EG800KEULCR07A07M04_01.300.01.300
                if (!trimmed.isEmpty() && !trimmed.startsWith("AT") && !trimmed.equals("OK")) {
                    return trimmed;
                }
            }
        }
        return "";
    }

    public Map<String, String> getModuleInfo() {
        Map<String, String> info = new LinkedHashMap<>();

        // 固件版本 (使用AT+QGMR)
        String version = getFirmwareVersion();
        if (!version.isEmpty()) {
            info.put("firmware_version", version);
            Matcher m = Pattern.compile("(\\d+\\.\\d+\\.\\d+\\.\\d+)$").matcher(version);
            if (m.find()) info.put("version_number", m.group(1));
        }

        // IMEI
        ATResponse resp = sendATCommand("AT+GSN");
        if (resp.success) {
            Matcher m = Pattern.compile("\\d{15}").matcher(resp.response);
            if (m.find()) info.put("imei", m.group());
        }

        // SIM卡状态
        resp = sendATCommand("AT+CPIN?");
        if (resp.success) {
            info.put("sim_status", resp.response.contains("READY") ? "已就绪" : resp.response);
        }

        return info;
    }

    public Map<String, String> checkNetworkStatus() {
        Map<String, String> status = new LinkedHashMap<>();

        // 网络注册状态
        ATResponse resp = sendATCommand("AT+CREG?");
        if (resp.success) {
            Matcher m = Pattern.compile("\\+CREG:\\s*\\d+,(\\d+)").matcher(resp.response);
            if (m.find()) {
                int regStatus = Integer.parseInt(m.group(1));
                Map<Integer, String> statusMap = Map.of(
                    0, "未注册", 1, "已注册(本地)", 2, "搜索中...",
                    3, "注册被拒绝", 4, "未知", 5, "已注册(漫游)"
                );
                status.put("network_reg", statusMap.getOrDefault(regStatus, "未知(" + regStatus + ")"));
            }
        }

        // 信号强度
        resp = sendATCommand("AT+CSQ");
        if (resp.success) {
            Matcher m = Pattern.compile("\\+CSQ:\\s*(\\d+),").matcher(resp.response);
            if (m.find()) {
                int rssi = Integer.parseInt(m.group(1));
                if (rssi == 99) {
                    status.put("signal", "未知或不可检测");
                } else {
                    int dbm = -113 + 2 * rssi;
                    status.put("signal", String.format("RSSI=%d (%ddBm)", rssi, dbm));
                }
            }
        }

        return status;
    }

    public ATResponse fotaUpgrade(String url, int autoReset, int timeout, BiConsumer<String, Integer> callback) {
        if (url.length() > 700) {
            return new ATResponse(false, "URL长度超过700字符限制");
        }

        this.progressCallback = callback;
        fotaComplete.set(false);
        fotaResult.set(-1);
        stopMonitor = false;

        System.out.println("\n" + "=".repeat(50));
        log("🔄 开始FOTA升级");
        System.out.println("=".repeat(50));

        // 1. 查询当前版本
        log("\n[步骤1] 查询当前固件版本...");
        String currentVersion = getFirmwareVersion();
        if (!currentVersion.isEmpty()) {
            log(String.format("📌 当前版本: %s", currentVersion));
        }

        // 2. 检查网络状态
        log("\n[步骤2] 检查网络状态...");
        Map<String, String> status = checkNetworkStatus();
        String netReg = status.getOrDefault("network_reg", "");
        if (!netReg.equals("已注册(本地)") && !netReg.equals("已注册(漫游)")) {
            return new ATResponse(false, String.format("网络未注册: %s", netReg));
        }
        log(String.format("✅ 网络已连接: %s", netReg));
        if (status.containsKey("signal")) {
            log(String.format("📶 信号强度: %s", status.get("signal")));
        }

        // 3. 发送FOTA升级指令
        log("\n[步骤3] 发送FOTA升级指令...");
        log(String.format("📎 URL: %s", url));
        log(String.format("📎 升级模式: %s", autoReset == 1 ? "自动重启" : "手动重启"));
        log(String.format("📎 超时时间: %d秒", timeout));

        // AT+QFOTADL="URL",升级模式,超时时间
        String cmd = String.format("AT+QFOTADL=\"%s\",%d,%d", url, autoReset, timeout);
        ATResponse result = sendATCommand(cmd, 5000);

        if (!result.success) {
            return new ATResponse(false, String.format("指令发送失败: %s", result.response));
        }

        log("✅ 指令发送成功，模组开始下载固件包...");
        log("\n[步骤4] 等待升级进度上报...");

        return new ATResponse(true, "FOTA升级已启动");
    }

    public boolean[] waitForFotaComplete(long maxWaitMs) {
        log(String.format("\n⏳ 等待升级完成（最长%d秒）...", maxWaitMs / 1000));

        long startTime = System.currentTimeMillis();
        while (System.currentTimeMillis() - startTime < maxWaitMs) {
            if (fotaComplete.get()) {
                int result = fotaResult.get();
                return new boolean[]{result == 0, true};
            }
            try {
                Thread.sleep(500);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                break;
            }
        }

        return new boolean[]{false, false}; // 超时
    }

    // ================== 工具方法 ==================

    public static void listSerialPorts() {
        System.out.println("\n📋 可用串口列表:");
        System.out.println("-".repeat(50));

        SerialPort[] ports = SerialPort.getCommPorts();
        if (ports.length == 0) {
            System.out.println("  未发现可用串口");
        } else {
            for (SerialPort port : ports) {
                System.out.printf("  %s%n", port.getSystemPortName());
                System.out.printf("    描述: %s%n", port.getDescriptivePortName());
            }
        }
        System.out.println();
    }

    public static void runBasicTest(EC800KDfotaTest modem) {
        System.out.println("\n" + "=".repeat(50));
        System.out.println("📡 EC800K/EG800K 基本测试");
        System.out.println("=".repeat(50));

        // AT测试
        System.out.println("\n[1/3] AT通信测试...");
        if (modem.testAT()) {
            System.out.println("✅ AT通信正常");
        } else {
            System.out.println("❌ AT通信失败");
            return;
        }

        // 模块信息
        System.out.println("\n[2/3] 获取模块信息...");
        Map<String, String> info = modem.getModuleInfo();
        info.forEach((key, value) -> System.out.printf("  %s: %s%n", key, value));

        // 网络状态
        System.out.println("\n[3/3] 检查网络状态...");
        Map<String, String> status = modem.checkNetworkStatus();
        status.forEach((key, value) -> System.out.printf("  %s: %s%n", key, value));
    }

    public static boolean runFotaTest(EC800KDfotaTest modem, String url, int autoReset, int timeout) {
        // 进度回调
        BiConsumer<String, Integer> onProgress = (status, value) -> {
            if (status.equals("UPDATING")) {
                int barLen = 30;
                int filled = barLen * value / 100;
                String bar = "█".repeat(filled) + "░".repeat(barLen - filled);
                System.out.printf("\r  [%s] %d%%", bar, value);
            } else if (status.equals("END")) {
                System.out.println();
            }
        };

        // 开始升级
        ATResponse result = modem.fotaUpgrade(url, autoReset, timeout, onProgress);
        if (!result.success) {
            log(String.format("❌ %s", result.response));
            return false;
        }

        // 等待完成
        boolean[] waitResult = modem.waitForFotaComplete(300000);
        boolean success = waitResult[0];
        boolean completed = waitResult[1];

        if (success) {
            log("\n[步骤5] 验证新版本...");
            try { Thread.sleep(5000); } catch (InterruptedException e) {}
            String newVersion = modem.getFirmwareVersion();
            if (!newVersion.isEmpty()) {
                log(String.format("📌 新版本: %s", newVersion));
            }
            log("✅ FOTA升级成功!");
        } else {
            if (!completed) {
                log("❌ 等待超时");
            } else {
                log(String.format("❌ 升级失败，错误码: %d", modem.fotaResult.get()));
            }
        }

        return success;
    }

    public static void printErrorCodes() {
        System.out.println("\n" + "=".repeat(50));
        System.out.println("📖 FOTA 错误码说明");
        System.out.println("=".repeat(50));

        System.out.println("\n【FOTA升级错误码】(+QIND: \"FOTA\",\"END\",<err>)");
        Map<Integer, String> dfotaErrors = new LinkedHashMap<>();
        dfotaErrors.put(0, "升级成功");
        dfotaErrors.put(504, "升级失败");
        dfotaErrors.put(505, "包校验出错");
        dfotaErrors.put(506, "固件MD5检查错误");
        dfotaErrors.put(507, "包版本不匹配");
        dfotaErrors.put(552, "包项目名不匹配");
        dfotaErrors.put(553, "包基线名不匹配");
        dfotaErrors.forEach((code, desc) -> System.out.printf("  %d: %s%n", code, desc));

        System.out.println("\n【+QIND URC上报说明】");
        System.out.println("  +QIND: \"FOTA\",\"HTTPSTART\"     - 开始HTTP下载");
        System.out.println("  +QIND: \"FOTA\",\"HTTPEND\",<err> - HTTP下载结束");
        System.out.println("  +QIND: \"FOTA\",\"UPDATING\",<%>  - 升级进度(7%-96%)");
        System.out.println("  +QIND: \"FOTA\",\"END\",<err>     - 升级结束(0=成功)");
    }

    public static void printUsage() {
        System.out.println("\n使用方法:");
        System.out.println("  java EC800KDfotaTest <串口> [命令] [参数...]");
        System.out.println("\n命令:");
        System.out.println("  test                   - 基本测试（默认）");
        System.out.println("  info                   - 显示错误码说明");
        System.out.println("  version                - 仅查询固件版本");
        System.out.println("  fota URL [mode] [timeout]");
        System.out.println("                         - FOTA升级");
        System.out.println("                           mode: 0=手动重启, 1=自动重启");
        System.out.println("\n示例:");
        System.out.println("  java EC800KDfotaTest /dev/ttyUSB0 test");
        System.out.println("  java EC800KDfotaTest COM3 fota \"http://server/fota.bin\" 0 50");
    }

    public static void main(String[] args) {
        System.out.println("=".repeat(50));
        System.out.println("🚀 EC800K/EG800K FOTA 测试工具 (Java)");
        System.out.println("   基于 Quectel DFOTA升级指导 V1.4");
        System.out.println("=".repeat(50));

        listSerialPorts();

        if (args.length < 1) {
            printUsage();
            return;
        }

        String portPath = args[0];
        String command = args.length > 1 ? args[1] : "test";

        if (command.equals("info")) {
            printErrorCodes();
            return;
        }

        EC800KDfotaTest modem = new EC800KDfotaTest(portPath);

        if (!modem.connect()) {
            System.out.println("\n💡 提示: 请检查串口连接和权限");
            return;
        }

        try {
            switch (command) {
                case "test":
                    runBasicTest(modem);
                    break;
                case "version":
                    String version = modem.getFirmwareVersion();
                    if (!version.isEmpty()) {
                        System.out.printf("%n📌 固件版本: %s%n", version);
                    } else {
                        System.out.println("\n❌ 无法获取版本");
                    }
                    break;
                case "fota":
                    if (args.length < 3) {
                        System.out.println("❌ 请提供FOTA包URL");
                        System.out.println("   用法: java EC800KDfotaTest <串口> fota <URL> [mode] [timeout]");
                    } else {
                        String url = args[2];
                        int autoReset = args.length > 3 ? Integer.parseInt(args[3]) : 0;
                        int timeout = args.length > 4 ? Integer.parseInt(args[4]) : 50;
                        runFotaTest(modem, url, autoReset, timeout);
                    }
                    break;
                default:
                    System.out.printf("❌ 未知命令: %s%n", command);
                    break;
            }
        } finally {
            modem.disconnect();
        }

        System.out.println("\n✨ 完成");
    }
}
