# EC800K/EG800K FOTA 升级工具

基于 Quectel LTE Standard(A) 系列 DFOTA 升级指导开发的 Python 串口工具。

## 安装

```bash
pip install pyserial
```

## 使用方法

```bash
python quick_fota.py <串口> <URL> [mode] [timeout]
```

### 参数说明

| 参数 | 说明 | 默认值 |
|------|------|--------|
| 串口 | 串口号，如 COM8 | 必填 |
| URL | FOTA固件下载地址 (HTTP) | 必填 |
| mode | 0=不自动重启, 1=自动重启 | 1 |
| timeout | 超时时间(秒) | 50 |

### 示例

```bash
# 升级
python quick_fota.py COM8 "http://server/signed_A04-A09.mini_1"

# 降级  
python quick_fota.py COM8 "http://server/signed_A09-A04.mini_1"

# 不自动重启，超时60秒
python quick_fota.py COM8 "http://server/firmware.mini_1" 0 60
```

## 注意事项

- **仅支持 HTTP**，MiniFOTA (4MB Flash) 不支持 HTTPS
- URL 最大长度 128 字符 (MiniFOTA 限制)
- 升级过程中请勿断电
- 升级完成后模块会自动重启 (mode=1)

## 错误码速查

| 错误码 | 说明 |
|--------|------|
| 0 | 成功 |
| 504 | 升级失败 |
| 505 | 包校验错误 |
| 507 | 版本不匹配 |
| 701 | HTTP未知错误 |
| 714 | DNS错误 |
| 727 | 等待数据超时 |
