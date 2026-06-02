from typing import Union

import serial
import time

from serial import Serial

STM32_port = serial.Serial(port='COM11', baudrate=115200, timeout=.1)
if STM32_port.is_open:
    print("打开串口成功！")
else:
    print("打开串口失败！")


def write(code_string):
    if STM32_port.is_open:
        STM32_port.write(bytes(code_string, 'utf-8'))
        time.sleep(0.002)
        return 0
    else:
        print("Cannot open the serial port.")


# def read():
#     if FPGA_port.is_open:
#         s=FPGA_port.read(64)
#         time.sleep(0.002)
#         return s
#     else:
#         print("Cannot open the serial port.")

def read():
    if STM32_port.is_open:
        # 等待直到有数据可读
        start_time = time.time()
        while STM32_port.in_waiting == 0:
            if time.time() - start_time > 0.1:  # 超时1秒
                return b''
            time.sleep(0.02)

        # 读取所有等待的数据
        bytes_to_read = STM32_port.in_waiting
        s = STM32_port.read(bytes_to_read)
        return s
    else:
        print("Cannot open the serial port.")
        return b''


while True:
    s = read()
    print(s)


#write('0x33')