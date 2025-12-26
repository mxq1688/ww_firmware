# EC800K DFOTA 测试工具 - C版 Makefile

CC = gcc
CFLAGS = -Wall -Wextra -O2
TARGET = ec800k_dfota_test

# 检测操作系统
UNAME_S := $(shell uname -s)
ifeq ($(UNAME_S),Linux)
    CFLAGS += -DLINUX
endif
ifeq ($(UNAME_S),Darwin)
    CFLAGS += -DMACOS
endif

.PHONY: all clean

all: $(TARGET)

$(TARGET): ec800k_dfota_test.c
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f $(TARGET)

# 使用示例
help:
	@echo "编译: make"
	@echo "清理: make clean"
	@echo ""
	@echo "运行示例:"
	@echo "  ./$(TARGET) /dev/ttyUSB0 test"
	@echo "  ./$(TARGET) /dev/ttyUSB0 dfota http://server/update.bin"

