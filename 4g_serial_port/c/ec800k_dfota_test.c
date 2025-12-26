/**
 * EC800K/EG800K FOTA 升级测试脚本 - C版
 * 基于 Quectel LTE Standard(A)系列 DFOTA 升级指导 V1.4
 *
 * 升级流程：
 * 1. 查询当前版本 (AT+QGMR)
 * 2. 发送升级指令 (AT+QFOTADL="URL",mode,timeout)
 * 3. 监听进度上报 (+QIND: "FOTA","UPDATING",进度)
 * 4. 等待升级完成 (+QIND: "FOTA","END",0)
 *
 * 编译 (Linux/macOS):
 *   gcc -o ec800k_dfota_test ec800k_dfota_test.c -Wall -lpthread
 * 
 * 编译 (Windows - MinGW):
 *   gcc -o ec800k_dfota_test.exe ec800k_dfota_test.c -Wall
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdbool.h>
#include <time.h>

#ifdef _WIN32
    #include <windows.h>
#else
    #include <fcntl.h>
    #include <termios.h>
    #include <unistd.h>
    #include <errno.h>
    #include <dirent.h>
    #include <sys/ioctl.h>
    #include <sys/time.h>
    #include <pthread.h>
#endif

#define DEFAULT_BAUDRATE 115200
#define AT_TIMEOUT_MS 2000
#define BUFFER_SIZE 1024

// ================== 日志函数 ==================

void log_msg(const char* format, ...) {
    time_t now;
    struct tm* tm_info;
    char time_buf[32];
    
    time(&now);
    tm_info = localtime(&now);
    strftime(time_buf, sizeof(time_buf), "%H:%M:%S", tm_info);
    
    printf("[%s] ", time_buf);
    
    va_list args;
    va_start(args, format);
    vprintf(format, args);
    va_end(args);
    printf("\n");
    fflush(stdout);
}

// ================== 串口操作 ==================

#ifdef _WIN32
typedef HANDLE SerialHandle;
#define INVALID_SERIAL INVALID_HANDLE_VALUE
#else
typedef int SerialHandle;
#define INVALID_SERIAL -1
#endif

typedef struct {
    SerialHandle handle;
    char port_path[256];
    int baud_rate;
    volatile bool stop_monitor;
    volatile bool fota_complete;
    volatile int fota_result;
} EC800KModem;

// 初始化模块结构
void modem_init(EC800KModem* modem, const char* port_path, int baud_rate) {
    modem->handle = INVALID_SERIAL;
    strncpy(modem->port_path, port_path, sizeof(modem->port_path) - 1);
    modem->baud_rate = baud_rate;
    modem->stop_monitor = false;
    modem->fota_complete = false;
    modem->fota_result = -1;
}

// 连接串口
bool modem_connect(EC800KModem* modem) {
#ifdef _WIN32
    char full_path[256];
    snprintf(full_path, sizeof(full_path), "\\\\.\\%s", modem->port_path);
    
    modem->handle = CreateFileA(
        full_path,
        GENERIC_READ | GENERIC_WRITE,
        0,
        NULL,
        OPEN_EXISTING,
        0,
        NULL
    );
    
    if (modem->handle == INVALID_HANDLE_VALUE) {
        log_msg("❌ 串口连接失败: %s (错误码: %lu)", modem->port_path, GetLastError());
        return false;
    }
    
    DCB dcb = {0};
    dcb.DCBlength = sizeof(DCB);
    
    if (!GetCommState(modem->handle, &dcb)) {
        CloseHandle(modem->handle);
        modem->handle = INVALID_HANDLE_VALUE;
        return false;
    }
    
    dcb.BaudRate = modem->baud_rate;
    dcb.ByteSize = 8;
    dcb.Parity = NOPARITY;
    dcb.StopBits = ONESTOPBIT;
    dcb.fBinary = TRUE;
    dcb.fDtrControl = DTR_CONTROL_ENABLE;
    dcb.fRtsControl = RTS_CONTROL_ENABLE;
    
    if (!SetCommState(modem->handle, &dcb)) {
        CloseHandle(modem->handle);
        modem->handle = INVALID_HANDLE_VALUE;
        return false;
    }
    
    COMMTIMEOUTS timeouts = {0};
    timeouts.ReadIntervalTimeout = 50;
    timeouts.ReadTotalTimeoutConstant = AT_TIMEOUT_MS;
    timeouts.ReadTotalTimeoutMultiplier = 10;
    timeouts.WriteTotalTimeoutConstant = 50;
    timeouts.WriteTotalTimeoutMultiplier = 10;
    SetCommTimeouts(modem->handle, &timeouts);
    
#else
    modem->handle = open(modem->port_path, O_RDWR | O_NOCTTY | O_NDELAY);
    
    if (modem->handle < 0) {
        log_msg("❌ 串口连接失败: %s (%s)", modem->port_path, strerror(errno));
        return false;
    }
    
    struct termios options;
    tcgetattr(modem->handle, &options);
    
    // 设置波特率
    speed_t speed;
    switch (modem->baud_rate) {
        case 9600:   speed = B9600; break;
        case 19200:  speed = B19200; break;
        case 38400:  speed = B38400; break;
        case 57600:  speed = B57600; break;
        case 115200: speed = B115200; break;
        default:     speed = B115200;
    }
    cfsetispeed(&options, speed);
    cfsetospeed(&options, speed);
    
    // 8N1
    options.c_cflag &= ~PARENB;
    options.c_cflag &= ~CSTOPB;
    options.c_cflag &= ~CSIZE;
    options.c_cflag |= CS8;
    options.c_cflag |= (CLOCAL | CREAD);
    
    // Raw模式
    options.c_lflag &= ~(ICANON | ECHO | ECHOE | ISIG);
    options.c_iflag &= ~(IXON | IXOFF | IXANY);
    options.c_oflag &= ~OPOST;
    
    // 超时设置
    options.c_cc[VMIN] = 0;
    options.c_cc[VTIME] = 20;  // 2秒超时
    
    tcsetattr(modem->handle, TCSANOW, &options);
    tcflush(modem->handle, TCIOFLUSH);
#endif
    
    log_msg("✅ 串口连接成功: %s @ %dbps", modem->port_path, modem->baud_rate);
    return true;
}

// 断开连接
void modem_disconnect(EC800KModem* modem) {
    modem->stop_monitor = true;
    if (modem->handle != INVALID_SERIAL) {
#ifdef _WIN32
        CloseHandle(modem->handle);
#else
        close(modem->handle);
#endif
        modem->handle = INVALID_SERIAL;
        log_msg("🔌 串口已断开");
    }
}

// 发送AT命令
bool modem_send_at_command(EC800KModem* modem, const char* cmd, char* response, size_t resp_size, int timeout_ms) {
    if (modem->handle == INVALID_SERIAL) {
        strcpy(response, "串口未连接");
        return false;
    }
    
    log_msg("📤 发送: %s", cmd);
    
    // 构建命令
    char full_cmd[512];
    snprintf(full_cmd, sizeof(full_cmd), "%s\r\n", cmd);
    
    // 清空缓冲区
    memset(response, 0, resp_size);
    
#ifdef _WIN32
    DWORD bytes_written;
    if (!WriteFile(modem->handle, full_cmd, strlen(full_cmd), &bytes_written, NULL)) {
        strcpy(response, "发送失败");
        return false;
    }
    
    // 读取响应
    DWORD bytes_read;
    DWORD total_read = 0;
    DWORD start_time = GetTickCount();
    
    while (GetTickCount() - start_time < (DWORD)timeout_ms && total_read < resp_size - 1) {
        char buf[256];
        if (ReadFile(modem->handle, buf, sizeof(buf) - 1, &bytes_read, NULL) && bytes_read > 0) {
            buf[bytes_read] = '\0';
            strncat(response, buf, resp_size - total_read - 1);
            total_read += bytes_read;
            
            if (strstr(response, "OK") || strstr(response, "ERROR")) {
                break;
            }
        }
        Sleep(50);
    }
#else
    ssize_t written = write(modem->handle, full_cmd, strlen(full_cmd));
    if (written < 0) {
        strcpy(response, "发送失败");
        return false;
    }
    
    // 读取响应
    size_t total_read = 0;
    int elapsed = 0;
    
    while (elapsed < timeout_ms && total_read < resp_size - 1) {
        char buf[256];
        ssize_t n = read(modem->handle, buf, sizeof(buf) - 1);
        if (n > 0) {
            buf[n] = '\0';
            strncat(response, buf, resp_size - total_read - 1);
            total_read += n;
            
            if (strstr(response, "OK") || strstr(response, "ERROR")) {
                break;
            }
        }
        usleep(50000);
        elapsed += 50;
    }
#endif
    
    // 去除首尾空白
    char* start = response;
    while (*start == '\r' || *start == '\n' || *start == ' ') start++;
    if (start != response) {
        memmove(response, start, strlen(start) + 1);
    }
    
    if (strlen(response) > 0) {
        log_msg("📥 响应: %s", response);
    }
    
    return strstr(response, "OK") != NULL;
}

// ================== 功能函数 ==================

bool modem_test_at(EC800KModem* modem) {
    char response[BUFFER_SIZE];
    return modem_send_at_command(modem, "AT", response, sizeof(response), AT_TIMEOUT_MS);
}

// 获取固件版本 (使用AT+QGMR)
void modem_get_firmware_version(EC800KModem* modem, char* version, size_t size) {
    char response[BUFFER_SIZE];
    version[0] = '\0';
    
    if (modem_send_at_command(modem, "AT+QGMR", response, sizeof(response), AT_TIMEOUT_MS)) {
        // 解析版本，跳过回显和OK
        char* line = strtok(response, "\r\n");
        while (line != NULL) {
            // 跳过AT命令回显和OK
            if (strncmp(line, "AT", 2) != 0 && strcmp(line, "OK") != 0 && strlen(line) > 0) {
                strncpy(version, line, size - 1);
                version[size - 1] = '\0';
                break;
            }
            line = strtok(NULL, "\r\n");
        }
    }
}

void modem_get_module_info(EC800KModem* modem) {
    char response[BUFFER_SIZE];
    char version[256];
    
    printf("\n模块信息:\n");
    
    // 固件版本 (使用AT+QGMR)
    modem_get_firmware_version(modem, version, sizeof(version));
    if (strlen(version) > 0) {
        printf("  firmware_version: %s\n", version);
    }
    
    // IMEI
    if (modem_send_at_command(modem, "AT+GSN", response, sizeof(response), AT_TIMEOUT_MS)) {
        printf("  IMEI响应: %s\n", response);
    }
    
    // SIM状态
    if (modem_send_at_command(modem, "AT+CPIN?", response, sizeof(response), AT_TIMEOUT_MS)) {
        if (strstr(response, "READY")) {
            printf("  sim_status: 已就绪\n");
        } else {
            printf("  sim_status: %s\n", response);
        }
    }
}

bool modem_check_network_status(EC800KModem* modem, char* net_reg, size_t size) {
    char response[BUFFER_SIZE];
    net_reg[0] = '\0';
    
    printf("\n网络状态:\n");
    
    // 网络注册
    if (modem_send_at_command(modem, "AT+CREG?", response, sizeof(response), AT_TIMEOUT_MS)) {
        // 解析 +CREG: x,y
        char* p = strstr(response, "+CREG:");
        if (p) {
            int n, stat;
            if (sscanf(p, "+CREG: %d,%d", &n, &stat) >= 2) {
                const char* status_str;
                switch (stat) {
                    case 0: status_str = "未注册"; break;
                    case 1: status_str = "已注册(本地)"; break;
                    case 2: status_str = "搜索中..."; break;
                    case 3: status_str = "注册被拒绝"; break;
                    case 5: status_str = "已注册(漫游)"; break;
                    default: status_str = "未知"; break;
                }
                strncpy(net_reg, status_str, size - 1);
                printf("  network_reg: %s\n", status_str);
            }
        }
    }
    
    // 信号强度
    if (modem_send_at_command(modem, "AT+CSQ", response, sizeof(response), AT_TIMEOUT_MS)) {
        char* p = strstr(response, "+CSQ:");
        if (p) {
            int rssi, ber;
            if (sscanf(p, "+CSQ: %d,%d", &rssi, &ber) >= 1) {
                if (rssi == 99) {
                    printf("  signal: 未知或不可检测\n");
                } else {
                    int dbm = -113 + 2 * rssi;
                    printf("  signal: RSSI=%d (%ddBm)\n", rssi, dbm);
                }
            }
        }
    }
    
    return (strcmp(net_reg, "已注册(本地)") == 0 || strcmp(net_reg, "已注册(漫游)") == 0);
}

bool modem_fota_upgrade(EC800KModem* modem, const char* url, int auto_reset, int timeout) {
    char response[BUFFER_SIZE];
    char version[256];
    char net_reg[64];
    
    if (strlen(url) > 700) {
        log_msg("❌ URL长度超过700字符限制");
        return false;
    }
    
    modem->fota_complete = false;
    modem->fota_result = -1;
    
    printf("\n==================================================\n");
    log_msg("🔄 开始FOTA升级");
    printf("==================================================\n");
    
    // 1. 查询当前版本
    log_msg("\n[步骤1] 查询当前固件版本...");
    modem_get_firmware_version(modem, version, sizeof(version));
    if (strlen(version) > 0) {
        log_msg("📌 当前版本: %s", version);
    }
    
    // 2. 检查网络状态
    log_msg("\n[步骤2] 检查网络状态...");
    if (!modem_check_network_status(modem, net_reg, sizeof(net_reg))) {
        log_msg("❌ 网络未注册: %s", net_reg);
        return false;
    }
    log_msg("✅ 网络已连接: %s", net_reg);
    
    // 3. 发送FOTA升级指令
    log_msg("\n[步骤3] 发送FOTA升级指令...");
    log_msg("📎 URL: %s", url);
    log_msg("📎 升级模式: %s", auto_reset == 1 ? "自动重启" : "手动重启");
    log_msg("📎 超时时间: %d秒", timeout);
    
    // AT+QFOTADL="URL",升级模式,超时时间
    char cmd[1024];
    snprintf(cmd, sizeof(cmd), "AT+QFOTADL=\"%s\",%d,%d", url, auto_reset, timeout);
    
    if (!modem_send_at_command(modem, cmd, response, sizeof(response), 5000)) {
        log_msg("❌ 指令发送失败: %s", response);
        return false;
    }
    
    log_msg("✅ 指令发送成功，模组开始下载固件包...");
    log_msg("\n[步骤4] 等待升级进度上报...");
    log_msg("(请通过串口监视器观察 +QIND: \"FOTA\",\"UPDATING\",进度 上报)");
    
    return true;
}

// ================== 工具函数 ==================

void list_serial_ports(void) {
    printf("\n📋 可用串口列表:\n");
    printf("--------------------------------------------------\n");

#ifdef _WIN32
    printf("  Windows平台请使用设备管理器查看COM端口\n");
    printf("  常见格式: COM1, COM2, COM3...\n");
#elif defined(__APPLE__)
    DIR* dir = opendir("/dev");
    if (dir) {
        struct dirent* entry;
        while ((entry = readdir(dir)) != NULL) {
            if (strstr(entry->d_name, "tty.usb") || strstr(entry->d_name, "cu.usb")) {
                printf("  /dev/%s\n", entry->d_name);
            }
        }
        closedir(dir);
    }
#else
    DIR* dir = opendir("/dev");
    if (dir) {
        struct dirent* entry;
        while ((entry = readdir(dir)) != NULL) {
            if (strstr(entry->d_name, "ttyUSB") || strstr(entry->d_name, "ttyACM")) {
                printf("  /dev/%s\n", entry->d_name);
            }
        }
        closedir(dir);
    }
#endif
    printf("\n");
}

void run_basic_test(EC800KModem* modem) {
    printf("\n==================================================\n");
    printf("📡 EC800K/EG800K 基本测试\n");
    printf("==================================================\n");
    
    printf("\n[1/3] AT通信测试...\n");
    if (modem_test_at(modem)) {
        printf("✅ AT通信正常\n");
    } else {
        printf("❌ AT通信失败\n");
        return;
    }
    
    printf("\n[2/3] 获取模块信息...\n");
    modem_get_module_info(modem);
    
    printf("\n[3/3] 检查网络状态...\n");
    char net_reg[64];
    modem_check_network_status(modem, net_reg, sizeof(net_reg));
}

void print_error_codes(void) {
    printf("\n==================================================\n");
    printf("📖 FOTA 错误码说明\n");
    printf("==================================================\n");
    
    printf("\n【FOTA升级错误码】(+QIND: \"FOTA\",\"END\",<err>)\n");
    printf("  0:   升级成功\n");
    printf("  504: 升级失败\n");
    printf("  505: 包校验出错\n");
    printf("  506: 固件MD5检查错误\n");
    printf("  507: 包版本不匹配\n");
    printf("  552: 包项目名不匹配\n");
    printf("  553: 包基线名不匹配\n");
    
    printf("\n【+QIND URC上报说明】\n");
    printf("  +QIND: \"FOTA\",\"HTTPSTART\"     - 开始HTTP下载\n");
    printf("  +QIND: \"FOTA\",\"HTTPEND\",<err> - HTTP下载结束\n");
    printf("  +QIND: \"FOTA\",\"UPDATING\",<%%>  - 升级进度(7%%-96%%)\n");
    printf("  +QIND: \"FOTA\",\"END\",<err>     - 升级结束(0=成功)\n");
}

void print_usage(const char* prog_name) {
    printf("\n使用方法:\n");
    printf("  %s <串口> [命令] [参数...]\n", prog_name);
    printf("\n命令:\n");
    printf("  test                   - 基本测试（默认）\n");
    printf("  info                   - 显示错误码说明\n");
    printf("  version                - 仅查询固件版本\n");
    printf("  fota URL [mode] [timeout]\n");
    printf("                         - FOTA升级\n");
    printf("                           mode: 0=手动重启, 1=自动重启\n");
    printf("\n示例:\n");
#ifdef _WIN32
    printf("  %s COM3 test\n", prog_name);
    printf("  %s COM3 fota \"http://server/fota.bin\" 0 50\n", prog_name);
#else
    printf("  %s /dev/ttyUSB0 test\n", prog_name);
    printf("  %s /dev/ttyUSB0 fota \"http://server/fota.bin\" 0 50\n", prog_name);
#endif
}

// ================== 主函数 ==================

int main(int argc, char* argv[]) {
    printf("==================================================\n");
    printf("🚀 EC800K/EG800K FOTA 测试工具 (C)\n");
    printf("   基于 Quectel DFOTA升级指导 V1.4\n");
    printf("==================================================\n");
    
    list_serial_ports();
    
    if (argc < 2) {
        print_usage(argv[0]);
        return 0;
    }
    
    const char* port = argv[1];
    const char* command = argc > 2 ? argv[2] : "test";
    
    if (strcmp(command, "info") == 0) {
        print_error_codes();
        return 0;
    }
    
    EC800KModem modem;
    modem_init(&modem, port, DEFAULT_BAUDRATE);
    
    if (!modem_connect(&modem)) {
        printf("\n💡 提示: 请检查串口连接和权限\n");
        return 1;
    }
    
    if (strcmp(command, "test") == 0) {
        run_basic_test(&modem);
    } else if (strcmp(command, "version") == 0) {
        char version[256];
        modem_get_firmware_version(&modem, version, sizeof(version));
        if (strlen(version) > 0) {
            printf("\n📌 固件版本: %s\n", version);
        } else {
            printf("\n❌ 无法获取版本\n");
        }
    } else if (strcmp(command, "fota") == 0) {
        if (argc < 4) {
            printf("❌ 请提供FOTA包URL\n");
            printf("   用法: %s <串口> fota <URL> [mode] [timeout]\n", argv[0]);
        } else {
            const char* url = argv[3];
            int auto_reset = argc > 4 ? atoi(argv[4]) : 0;
            int timeout = argc > 5 ? atoi(argv[5]) : 50;
            modem_fota_upgrade(&modem, url, auto_reset, timeout);
        }
    } else {
        printf("❌ 未知命令: %s\n", command);
    }
    
    modem_disconnect(&modem);
    printf("\n✨ 完成\n");
    
    return 0;
}
