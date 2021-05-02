import time
from pyftdi.ftdi import Ftdi, FtdiError
from usb.core import USBError
from pydispatch import dispatcher

class KlineAdapter(Ftdi):

    def __init__(self, device, baudrate=10400, retries=1, timeout=0.1, kline_timeout=0.1, kline_wait=0.002, kline_testbytes=1):
        self.retries = retries
        self.timeout = timeout
        self.kline_timeout = kline_timeout
        self.kline_wait = kline_wait
        self.kline_testbytes = kline_testbytes
        super(KlineAdapter, self).__init__()
        self.open_from_device(device)
        self.set_baudrate(baudrate)
        self.set_line_property(8, 1, 'N')
        self.kline = self.kline_loopback_ping
        self.stats = {'retries':0, 
         'checksum_errors':0, 
         'unneeded_retry':0, 
         'usb_busy':0}
        dispatcher.send(signal='ecu.stats', sender=self, data=(self.stats))

    def kline_loopback_ping(self):
        ret = False
        starttime = time.time()
        msg = b'\xff' * self.kline_testbytes
        nbytes = 2 + self.kline_testbytes
        while True:
            try:
                self.purge_buffers()
                if self._write(msg) == self.kline_testbytes:
                    time.sleep(self.kline_wait)
                    tmp = self._read()
                    if len(tmp) == nbytes:
                        ret = True
                        break
                if time.time() - starttime > self.kline_timeout:
                    break
            except USBError as e:
                try:
                    if e.errno == 2:
                        self.stats['usb_busy'] += 1
                    else:
                        dispatcher.send(signal='usberror', sender=self, errno=(e.errno), strerror=(e.strerror))
                        break
                finally:
                    e = None
                    del e

            except FtdiError as e:
                try:
                    ee = str(e)
                    err = int(ee.split(':')[1].split('[')[1].split(']')[0].split(' ')[1])
                    if err == 2:
                        self.dev.stats['usb_busy'] += 1
                    else:
                        dispatcher.send(signal='ftdierror', sender=self, errno=err, strerror=ee)
                        break
                finally:
                    e = None
                    del e

        self.purge_buffers()
        return ret


class ECU(object):

    def __init__(self, klineadapter):
        self.dev = klineadapter
