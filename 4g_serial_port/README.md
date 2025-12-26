# EC800K/EG800K FOTA 升级测试工具

基于 **Quectel LTE Standard(A)系列 DFOTA 升级指导 V1.4** 文档开发的多语言串口测试脚本。

## 🔄 FOTA升级流程

```
┌─────────────────────────────────────────────────────────────────┐
│  1. AT+QGMR          → 查询当前版本                              │
│  2. AT+QFOTADL="URL",0,50  → 发送升级指令                        │
│  3. 模组下载固件包（后台进行，约26秒）                             │
│  4. +QIND: "FOTA","UPDATING",7   → 第一次重启，开始升级           │
│  5. +QIND: "FOTA","UPDATING",47  → 进度7%-47%                    │
│  6. +QIND: "FOTA","UPDATING",60  → 第二次重启，继续升级           │
│  7. +QIND: "FOTA","UPDATING",96  → 进度60%-96%                   │
│  8. +QIND: "FOTA","END",0        → 升级完成(0=成功)              │
│  9. 模组最后一次重启，加载新固件                                  │
└─────────────────────────────────────────────────────────────────┘
```

## 📁 项目结构

```
4g_serial_port/
├── README.md                 # 本文件
│
├── python/                   # 🐍 Python版 (推荐)
│   ├── ec800k_dfota_test.py
│   ├── requirements.txt
│   └── README.md
│
├── nodejs/                   # 📦 Node.js版
│   ├── ec800k_dfota_test.js
│   └── package.json
│
├── golang/                   # Go版
│   ├── main.go
│   └── go.mod
│
├── rust/                     # Rust版
│   ├── src/main.rs
│   └── Cargo.toml
│
├── c/                        # C版
│   ├── ec800k_dfota_test.c
│   └── Makefile
│
├── csharp/                   # C#版
│   ├── EC800KDfotaTest.cs
│   └── EC800KDfotaTest.csproj
│
└── java/                     # Java版
    ├── src/main/java/EC800KDfotaTest.java
    └── pom.xml
```

## 🚀 各语言快速开始

### Python (推荐)

```bash
cd python
pip install -r requirements.txt

# 基本测试
python ec800k_dfota_test.py /dev/ttyUSB0 test

# 查询版本
python ec800k_dfota_test.py /dev/ttyUSB0 version

# FOTA升级
python ec800k_dfota_test.py /dev/ttyUSB0 fota "http://server/fota.bin" 0 50
```

### Node.js

```bash
cd nodejs
npm install
node ec800k_dfota_test.js /dev/ttyUSB0 test
```

### Go

```bash
cd golang
go mod tidy
go run main.go /dev/ttyUSB0 test
```

### Rust

```bash
cd rust
cargo run -- /dev/ttyUSB0 test
```

### C

```bash
cd c
make
./ec800k_dfota_test /dev/ttyUSB0 test
```

### C#

```bash
cd csharp
dotnet run -- /dev/ttyUSB0 test
```

### Java

```bash
cd java
mvn compile exec:java -Dexec.args="/dev/ttyUSB0 test"
```

## 📋 功能说明

所有语言版本都支持以下功能：

| 命令 | 说明 |
|------|------|
| `test` | 基本AT通信测试、模块信息查询、网络状态检查 |
| `info` | 显示DFOTA错误码说明 |
| `dfota <URL>` | 通过HTTP/FTP下载并执行DFOTA升级 |

## 🔧 支持的AT命令

| 命令 | 功能 |
|------|------|
| `AT` | 通信测试 |
| `ATI` | 模块信息 |
| `AT+GSN` | 查询IMEI |
| `AT+QGMR` | 查询固件版本 ⭐ |
| `AT+CPIN?` | SIM卡状态 |
| `AT+CREG?` | 网络注册状态 |
| `AT+CSQ` | 信号强度 |
| `AT+QFOTADL="URL",mode,timeout` | FOTA升级 ⭐ |

### FOTA升级指令详解

```
AT+QFOTADL="http://server/fota.bin",0,50
           ├── URL: 固件包下载地址（最长700字符）
           ├── mode: 0=手动重启, 1=自动重启
           └── timeout: 超时时间（秒）
```

### +QIND URC上报说明

| URC | 说明 |
|-----|------|
| `+QIND: "FOTA","HTTPSTART"` | 开始HTTP下载 |
| `+QIND: "FOTA","HTTPEND",<err>` | HTTP下载结束 |
| `+QIND: "FOTA","START"` | 开始升级 |
| `+QIND: "FOTA","UPDATING",<%>` | 升级进度(7%-96%) |
| `+QIND: "FOTA","END",<err>` | 升级结束(0=成功) |

## 📊 语言对比

| 语言 | 库/框架 | 优点 | 适用场景 |
|------|---------|------|----------|
| **Python** | pyserial | 最简单，跨平台 | 快速开发、测试 |
| **Node.js** | serialport | 异步，事件驱动 | Web集成 |
| **Go** | go-serial | 编译为单文件 | 部署工具 |
| **Rust** | serialport-rs | 高性能，安全 | 嵌入式工具 |
| **C** | termios/Win32 | 最底层，最快 | 资源受限环境 |
| **C#** | System.IO.Ports | Windows友好 | Windows工具 |
| **Java** | jSerialComm | 跨平台，企业级 | 企业应用 |

## 📖 DFOTA 升级流程

1. 确保模块已入网（`AT+CREG?` 返回 1 或 5）
2. 准备DFOTA差分包并上传到服务器
3. 执行 `AT+QFOTADL="<URL>"` 命令
4. 等待模块下载、校验并重启

## ⚠️ 错误码速查

### DFOTA 升级错误

| 错误码 | 说明 |
|--------|------|
| 0 | 升级成功 |
| 504 | 升级失败 |
| 505 | 包校验出错 |
| 506 | 固件MD5检查错误 |
| 507 | 包版本不匹配 |
| 552 | 包项目名不匹配 |
| 553 | 包基线名不匹配 |

### HTTP 下载错误

| 错误码 | 说明 |
|--------|------|
| 0 | 下载成功 |
| 701 | 未知错误 |
| 702 | 超时 |
| 711 | URL错误 |
| 714 | DNS错误 |
| 716 | Socket连接错误 |

### FTP 下载错误

| 错误码 | 说明 |
|--------|------|
| 0 | 下载成功 |
| 601 | 未知错误 |
| 602 | 超时 |
| 611 | 打开文件失败 |
| 625 | 登录失败 |

## 🔌 串口路径示例

| 操作系统 | 示例路径 |
|----------|----------|
| macOS | `/dev/tty.usbserial-1420` |
| Linux | `/dev/ttyUSB0`, `/dev/ttyACM0` |
| Windows | `COM3`, `COM4` |

## 📝 注意事项

- URL最大长度为700字符
- DFOTA升级过程中请勿断电
- 升级完成后模块会自动重启
- 建议在信号良好的环境下进行升级

## 📚 参考文档

- Quectel_LTE_Standard(A)系列_DFOTA_升级指导_V1.4.pdf
