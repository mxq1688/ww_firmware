#!/usr/bin/env node
/**
 * EC800K/EG800K FOTA 升级测试脚本 - Node.js版
 * 基于 Quectel LTE Standard(A)系列 DFOTA 升级指导 V1.4
 * 
 * 升级流程：
 * 1. 查询当前版本 (AT+QGMR)
 * 2. 发送升级指令 (AT+QFOTADL="URL",mode,timeout)
 * 3. 监听进度上报 (+QIND: "FOTA","UPDATING",进度)
 * 4. 等待升级完成 (+QIND: "FOTA","END",0)
 * 5. 模组重启，验证新版本
 * 
 * 依赖: npm install serialport
 */

const { SerialPort } = require('serialport');
const { ReadlineParser } = require('@serialport/parser-readline');

// ================== 配置 ==================
const DEFAULT_BAUDRATE = 115200;
const AT_TIMEOUT = 2000; // ms

// 带时间戳的日志
function log(msg) {
    const now = new Date();
    const timestamp = now.toTimeString().split(' ')[0] + '.' + 
                      String(now.getMilliseconds()).padStart(3, '0');
    console.log(`[${timestamp}] ${msg}`);
}

class EC800KModem {
    constructor(portPath, baudRate = DEFAULT_BAUDRATE) {
        this.portPath = portPath;
        this.baudRate = baudRate;
        this.port = null;
        this.parser = null;
        this.responseBuffer = '';
        this.responseResolve = null;
        this.progressCallback = null;
        this.fotaComplete = false;
        this.fotaResult = -1;
    }

    async connect() {
        return new Promise((resolve, reject) => {
            this.port = new SerialPort({
                path: this.portPath,
                baudRate: this.baudRate,
            });

            this.parser = this.port.pipe(new ReadlineParser({ delimiter: '\r\n' }));

            this.parser.on('data', (line) => {
                line = line.trim();
                if (!line) return;

                // 解析 +QIND: "FOTA","UPDATING",进度
                const updateMatch = line.match(/\+QIND:\s*"FOTA"\s*,\s*"UPDATING"\s*,\s*(\d+)/);
                if (updateMatch) {
                    const progress = parseInt(updateMatch[1]);
                    log(`📊 升级进度: ${progress}%`);
                    if (this.progressCallback) {
                        this.progressCallback('UPDATING', progress);
                    }
                    return;
                }

                // 解析 +QIND: "FOTA","END",结果码
                const endMatch = line.match(/\+QIND:\s*"FOTA"\s*,\s*"END"\s*,\s*(\d+)/);
                if (endMatch) {
                    const result = parseInt(endMatch[1]);
                    this.fotaComplete = true;
                    this.fotaResult = result;
                    if (result === 0) {
                        log('✅ FOTA升级完成!');
                    } else {
                        log(`❌ FOTA升级失败，错误码: ${result}`);
                    }
                    if (this.progressCallback) {
                        this.progressCallback('END', result);
                    }
                    return;
                }

                // 其他 +QIND 消息
                if (line.includes('+QIND:')) {
                    log(`📨 ${line}`);
                    return;
                }

                // 开机信息
                if (['RDY', '+CFUN: 1'].includes(line) || 
                    line.startsWith('+CPIN:') || 
                    line.startsWith('+QUSIM:')) {
                    log(`📨 开机信息: ${line}`);
                    return;
                }

                // 普通响应
                this.responseBuffer += line + '\n';
                if (line.includes('OK') || line.includes('ERROR')) {
                    if (this.responseResolve) {
                        this.responseResolve(this.responseBuffer);
                        this.responseResolve = null;
                    }
                }
            });

            this.port.on('open', () => {
                log(`✅ 串口连接成功: ${this.portPath} @ ${this.baudRate}bps`);
                resolve(true);
            });

            this.port.on('error', (err) => {
                log(`❌ 串口错误: ${err.message}`);
                reject(err);
            });
        });
    }

    disconnect() {
        if (this.port && this.port.isOpen) {
            this.port.close();
            log('🔌 串口已断开');
        }
    }

    async sendATCommand(cmd, timeout = AT_TIMEOUT) {
        return new Promise((resolve) => {
            this.responseBuffer = '';
            log(`📤 发送: ${cmd}`);

            const timer = setTimeout(() => {
                if (this.responseResolve) {
                    this.responseResolve = null;
                    resolve({ success: false, response: '超时' });
                }
            }, timeout);

            this.responseResolve = (response) => {
                clearTimeout(timer);
                const trimmed = response.trim();
                if (trimmed) log(`📥 响应: ${trimmed}`);
                resolve({
                    success: response.includes('OK'),
                    response: trimmed
                });
            };

            this.port.write(`${cmd}\r\n`);
        });
    }

    // ================== 基本AT命令 ==================

    async testAT() {
        const { success } = await this.sendATCommand('AT');
        return success;
    }

    async getFirmwareVersion() {
        // 使用 AT+QGMR 查询版本
        const result = await this.sendATCommand('AT+QGMR');
        if (result.success) {
            const lines = result.response.split('\n');
            for (const line of lines) {
                const trimmed = line.trim();
                // 版本格式: EG800KEULCR07A07M04_01.300.01.300
                if (trimmed && !trimmed.startsWith('AT') && trimmed !== 'OK') {
                    return trimmed;
                }
            }
        }
        return '';
    }

    async getModuleInfo() {
        const info = {};

        // 固件版本 (使用AT+QGMR)
        const version = await this.getFirmwareVersion();
        if (version) {
            info.firmwareVersion = version;
            const match = version.match(/(\d+\.\d+\.\d+\.\d+)$/);
            if (match) info.versionNumber = match[1];
        }

        // IMEI
        let result = await this.sendATCommand('AT+GSN');
        if (result.success) {
            const match = result.response.match(/\d{15}/);
            if (match) info.imei = match[0];
        }

        // SIM卡状态
        result = await this.sendATCommand('AT+CPIN?');
        if (result.success) {
            info.simStatus = result.response.includes('READY') ? '已就绪' : result.response;
        }

        return info;
    }

    async checkNetworkStatus() {
        const status = {};

        // 网络注册状态
        let result = await this.sendATCommand('AT+CREG?');
        if (result.success) {
            const match = result.response.match(/\+CREG:\s*\d+,(\d+)/);
            if (match) {
                const regStatus = parseInt(match[1]);
                const statusMap = {
                    0: '未注册', 1: '已注册(本地)', 2: '搜索中...',
                    3: '注册被拒绝', 4: '未知', 5: '已注册(漫游)'
                };
                status.networkReg = statusMap[regStatus] || `未知(${regStatus})`;
            }
        }

        // 信号强度
        result = await this.sendATCommand('AT+CSQ');
        if (result.success) {
            const match = result.response.match(/\+CSQ:\s*(\d+),/);
            if (match) {
                const rssi = parseInt(match[1]);
                if (rssi === 99) {
                    status.signal = '未知或不可检测';
                } else {
                    const dbm = -113 + 2 * rssi;
                    status.signal = `RSSI=${rssi} (${dbm}dBm)`;
                }
            }
        }

        return status;
    }

    // ================== FOTA 命令 ==================

    async fotaUpgrade(url, autoReset = 0, timeout = 50, progressCallback = null) {
        if (url.length > 700) {
            return { success: false, response: 'URL长度超过700字符限制' };
        }

        this.progressCallback = progressCallback;
        this.fotaComplete = false;
        this.fotaResult = -1;

        console.log('\n' + '='.repeat(50));
        log('🔄 开始FOTA升级');
        console.log('='.repeat(50));

        // 1. 查询当前版本
        log('\n[步骤1] 查询当前固件版本...');
        const currentVersion = await this.getFirmwareVersion();
        if (currentVersion) {
            log(`📌 当前版本: ${currentVersion}`);
        }

        // 2. 检查网络状态
        log('\n[步骤2] 检查网络状态...');
        const status = await this.checkNetworkStatus();
        if (!['已注册(本地)', '已注册(漫游)'].includes(status.networkReg)) {
            return { success: false, response: `网络未注册: ${status.networkReg || '未知'}` };
        }
        log(`✅ 网络已连接: ${status.networkReg}`);

        // 3. 发送FOTA升级指令
        log('\n[步骤3] 发送FOTA升级指令...');
        log(`📎 URL: ${url}`);
        log(`📎 升级模式: ${autoReset === 1 ? '自动重启' : '手动重启'}`);
        log(`📎 超时时间: ${timeout}秒`);

        // AT+QFOTADL="URL",升级模式,超时时间
        const cmd = `AT+QFOTADL="${url}",${autoReset},${timeout}`;
        const result = await this.sendATCommand(cmd, 5000);

        if (!result.success) {
            return { success: false, response: `指令发送失败: ${result.response}` };
        }

        log('✅ 指令发送成功，模组开始下载固件包...');
        log('\n[步骤4] 等待升级进度上报...');

        return { success: true, response: 'FOTA升级已启动' };
    }

    async waitForFotaComplete(maxWait = 300000) {
        log(`\n⏳ 等待升级完成（最长${maxWait / 1000}秒）...`);

        return new Promise((resolve) => {
            const startTime = Date.now();
            const checkInterval = setInterval(() => {
                if (this.fotaComplete) {
                    clearInterval(checkInterval);
                    resolve({ success: this.fotaResult === 0, resultCode: this.fotaResult });
                } else if (Date.now() - startTime > maxWait) {
                    clearInterval(checkInterval);
                    resolve({ success: false, resultCode: -1 });
                }
            }, 500);
        });
    }
}

// ================== 工具函数 ==================

async function listSerialPorts() {
    const ports = await SerialPort.list();
    console.log('\n📋 可用串口列表:');
    console.log('-'.repeat(50));

    if (ports.length === 0) {
        console.log('  未发现可用串口');
    } else {
        ports.forEach(port => {
            console.log(`  ${port.path}`);
            console.log(`    制造商: ${port.manufacturer || '未知'}`);
        });
    }
    console.log();
    return ports;
}

async function runBasicTest(modem) {
    console.log('\n' + '='.repeat(50));
    console.log('📡 EC800K/EG800K 基本测试');
    console.log('='.repeat(50));

    // AT测试
    console.log('\n[1/3] AT通信测试...');
    if (await modem.testAT()) {
        console.log('✅ AT通信正常');
    } else {
        console.log('❌ AT通信失败');
        return false;
    }

    // 模块信息
    console.log('\n[2/3] 获取模块信息...');
    const info = await modem.getModuleInfo();
    Object.entries(info).forEach(([key, value]) => {
        console.log(`  ${key}: ${value}`);
    });

    // 网络状态
    console.log('\n[3/3] 检查网络状态...');
    const status = await modem.checkNetworkStatus();
    Object.entries(status).forEach(([key, value]) => {
        console.log(`  ${key}: ${value}`);
    });

    return true;
}

async function runFotaTest(modem, url, autoReset = 0, timeout = 50) {
    // 进度回调
    const onProgress = (status, value) => {
        if (status === 'UPDATING') {
            const barLen = 30;
            const filled = Math.floor(barLen * value / 100);
            const bar = '█'.repeat(filled) + '░'.repeat(barLen - filled);
            process.stdout.write(`\r  [${bar}] ${value}%`);
        } else if (status === 'END') {
            console.log(); // 换行
        }
    };

    // 开始升级
    const result = await modem.fotaUpgrade(url, autoReset, timeout, onProgress);
    if (!result.success) {
        log(`❌ ${result.response}`);
        return false;
    }

    // 等待完成
    const { success, resultCode } = await modem.waitForFotaComplete(300000);

    if (success) {
        log('\n[步骤5] 验证新版本...');
        await new Promise(r => setTimeout(r, 5000));
        const newVersion = await modem.getFirmwareVersion();
        if (newVersion) {
            log(`📌 新版本: ${newVersion}`);
        }
        log('✅ FOTA升级成功!');
    } else {
        if (resultCode === -1) {
            log('❌ 等待超时');
        } else {
            log(`❌ 升级失败，错误码: ${resultCode}`);
        }
    }

    return success;
}

function printErrorCodes() {
    console.log('\n' + '='.repeat(50));
    console.log('📖 FOTA 错误码说明');
    console.log('='.repeat(50));

    console.log('\n【FOTA升级错误码】(+QIND: "FOTA","END",<err>)');
    const dfotaErrors = {
        0: '升级成功', 504: '升级失败', 505: '包校验出错',
        506: '固件MD5检查错误', 507: '包版本不匹配',
        552: '包项目名不匹配', 553: '包基线名不匹配'
    };
    Object.entries(dfotaErrors).forEach(([code, desc]) => {
        console.log(`  ${code}: ${desc}`);
    });

    console.log('\n【+QIND URC上报说明】');
    console.log('  +QIND: "FOTA","HTTPSTART"     - 开始HTTP下载');
    console.log('  +QIND: "FOTA","HTTPEND",<err> - HTTP下载结束');
    console.log('  +QIND: "FOTA","UPDATING",<%>  - 升级进度(7%-96%)');
    console.log('  +QIND: "FOTA","END",<err>     - 升级结束(0=成功)');
}

// ================== 主函数 ==================

async function main() {
    console.log('='.repeat(50));
    console.log('🚀 EC800K/EG800K FOTA 测试工具 (Node.js)');
    console.log('   基于 Quectel DFOTA升级指导 V1.4');
    console.log('='.repeat(50));

    await listSerialPorts();

    const args = process.argv.slice(2);

    if (args.length < 1) {
        console.log('\n使用方法:');
        console.log('  node ec800k_dfota_test.js <串口> [命令] [参数...]');
        console.log('\n命令:');
        console.log('  test                   - 基本测试（默认）');
        console.log('  info                   - 显示错误码说明');
        console.log('  version                - 仅查询固件版本');
        console.log('  fota URL [mode] [timeout]');
        console.log('                         - FOTA升级');
        console.log('                           mode: 0=手动重启, 1=自动重启');
        console.log('\n示例:');
        console.log('  node ec800k_dfota_test.js /dev/ttyUSB0 test');
        console.log('  node ec800k_dfota_test.js COM3 fota "http://server/fota.bin" 0 50');
        return;
    }

    const port = args[0];
    const command = args[1] || 'test';

    if (command === 'info') {
        printErrorCodes();
        return;
    }

    const modem = new EC800KModem(port);

    try {
        await modem.connect();

        if (command === 'test') {
            await runBasicTest(modem);
        } else if (command === 'version') {
            const version = await modem.getFirmwareVersion();
            if (version) {
                console.log(`\n📌 固件版本: ${version}`);
            } else {
                console.log('\n❌ 无法获取版本');
            }
        } else if (command === 'fota') {
            if (args.length < 3) {
                console.log('❌ 请提供FOTA包URL');
                console.log('   用法: node script.js <串口> fota <URL> [mode] [timeout]');
            } else {
                const url = args[2];
                const autoReset = parseInt(args[3]) || 0;
                const timeout = parseInt(args[4]) || 50;
                await runFotaTest(modem, url, autoReset, timeout);
            }
        } else {
            console.log(`❌ 未知命令: ${command}`);
        }
    } catch (err) {
        console.log(`❌ 错误: ${err.message}`);
    } finally {
        modem.disconnect();
    }

    console.log('\n✨ 完成');
}

main();
