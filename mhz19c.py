from machine import UART, Pin
import time

class MHZ19BSensor:
    # initializes a new instance
#    def __init__(self, tx_pin:Pin, rx_pin:Pin):
    def __init__(self, tx_pin:Pin, rx_pin:Pin):
        self.uart = UART(1, baudrate=9600, tx=tx_pin, rx=rx_pin, bits=8, parity=None, stop=1)
#        self.lights = lights
#        self.co2_threshold = int(co2_threshold)

    # measure CO2
    def measure(self):
        while True:
            # send a read command to the sensor
            self.uart.write(b'\xff\x01\x86\x00\x00\x00\x00\x00\x79')

            # a little delay to let the sensor measure CO2 and send the data back
            time.sleep(1)  # in seconds

            # read and validate the data
            buf = self.uart.read(9)
            if self.is_valid(buf):
                break

            # retry if the data is wrong
#            self.lights.error_on()
            print('error while reading MH-Z19B sensor: invalid data')
            print('retry ...')

#        self.lights.error_off()

        co2 = buf[2] * 256 + buf[3]
        #print('co2         = %.2f' % co2)

        # turn on the LED if the CO2 level is higher than the threshold
#        if co2 > self.co2_threshold:
#            self.lights.high_co2_on()
#        else:
#            self.lights.high_co2_off()

        return [co2]

    # check data returned by the sensor
    def is_valid(self, buf):
        if buf is None or buf[0] != 0xFF or buf[1] != 0x86:
            return False
        i = 1
        checksum = 0x00
        while i < 8:
            checksum += buf[i] % 256
            i += 1
        checksum = ~checksum & 0xFF
        checksum += 1
        return checksum == buf[8]