import struct, time
from usb.core import USBError
from pyftdi.ftdi import FtdiError
from array import array
from enum import Enum, auto
from pydispatch import dispatcher
from base import ECU

class ECUSTATE(Enum):
    OFF = auto()
    UNKNOWN = auto()
    OK = auto()
    FLASH = auto()
    SECURE = auto()
    RECOVER_OLD = auto()
    RECOVER_NEW = auto()


DTC = {'01-01':'MAP sensor circuit low voltage', 
 '01-02':'MAP sensor circuit high voltage', 
 '02-01':'MAP sensor performance problem', 
 '07-01':'ECT sensor circuit low voltage', 
 '07-02':'ECT sensor circuit high voltage', 
 '08-01':'TP sensor circuit low voltage', 
 '08-02':'TP sensor circuit high voltage', 
 '09-01':'IAT sensor circuit low voltage', 
 '09-02':'IAT sensor circuit high voltage', 
 '11-01':'VS sensor no signal', 
 '12-01':'No.1 primary injector circuit malfunction', 
 '13-01':'No.2 primary injector circuit malfunction', 
 '14-01':'No.3 primary injector circuit malfunction', 
 '15-01':'No.4 primary injector circuit malfunction', 
 '16-01':'No.1 secondary injector circuit malfunction', 
 '17-01':'No.2 secondary injector circuit malfunction', 
 '18-01':'CMP sensor no signal', 
 '19-01':'CKP sensor no signal', 
 '21-01':'0₂ sensor low voltage', 
 '21-02':'0₂ sensor high voltage', 
 '23-01':'0₂ sensor heater malfunction', 
 '25-02':'Knock sensor circuit malfunction', 
 '25-03':'Knock sensor circuit malfunction', 
 '29-01':'IACV circuit malfunction', 
 '33-02':'ECM EEPROM malfunction', 
 '34-01':'ECV POT low voltage malfunction', 
 '34-02':'ECV POT high voltage malfunction', 
 '35-01':'EGCA malfunction', 
 '36-01':'A/F sensor malfunction', 
 '38-01':'A/F sensor heater malfunction', 
 '48-01':'No.3 secondary injector circuit malfunction', 
 '49-01':'No.4 secondary injector circuit malfunction', 
 '51-01':'HESD linear solenoid malfunction', 
 '54-01':'Bank angle sensor circuit low voltage', 
 '54-02':'Bank angle sensor circuit high voltage', 
 '56-01':'Knock sensor IC malfunction', 
 '82-01':'Fast idle solenoid valve malfunction', 
 '86-01':'Serial communication malfunction', 
 '88-01':'EVAP purge control solenoid valve malfunction', 
 '91-01':'Ignition coil primary circuit malfunction'}

def format_read(location):
    tmp = struct.unpack('>4B', struct.pack('>I', location))
    return [tmp[1], tmp[3], tmp[2]]


def checksum8bitHonda(data):
    return (sum(bytearray(data)) ^ 255) + 1 & 255


def checksum8bit(data):
    return 255 - (sum(bytearray(data)) - 1 >> 8)


def validate_checksums(byts, nbyts, cksum):
    fixed = False
    if 0 <= cksum < nbyts:
        byts[cksum] = checksum8bitHonda(byts[:cksum] + byts[cksum + 1:])
        fixed = True
    ret = checksum8bitHonda(byts) == 0
    return (ret, fixed, byts)


def do_validation(byts, nbyts, cksum=-1):
    status = 'good'
    ret, fixed, byts = validate_checksums(byts, nbyts, cksum)
    if not ret:
        status = 'bad'
    else:
        if fixed:
            status = 'fixed'
    return (
     ret, status, byts)


def format_message(mtype, data):
    ml = len(mtype)
    dl = len(data)
    msgsize = 2 + ml + dl
    msg = mtype + [msgsize] + data
    msg += [checksum8bitHonda(msg)]
    assert msg[ml] == len(msg)
    return (msg, ml, dl)


class HondaECU(ECU):

    def init(self):
        while True:
            try:
                self.dev.set_bitmode(1, 1)
                self.dev._write(b'\x00')
                time.sleep(0.07)
                self.dev._write(b'\x01')
                self.dev.set_bitmode(0, 0)
                time.sleep(0.2)
                self.dev._read()
                break
            except USBError as e:
                try:
                    if e.errno not in (2, ):
                        self.dev.stats['usb_busy'] += 1
                        break
                finally:
                    e = None
                    del e

            except FtdiError as e:
                try:
                    print('FTDI honda init', e)
                    break
                finally:
                    e = None
                    del e

    def send(self, buf, ml):
        timeout = self.dev.timeout
        while True:
            try:
                msg = ''.join([chr(b) for b in buf]).encode('latin1')
                mlen = len(msg)
                if self.dev._write(msg) == mlen:
                    readbuffer = array('B')
                    r = mlen + ml + 1
                    starttime = time.time()
                    while len(readbuffer) < r:
                        tempbuf = self.dev._read()
                        length = len(tempbuf)
                        i = 0
                        if length > 2:
                            while i < length:
                                readbuffer += tempbuf[i + 2:i + 64]
                                i += 64

                        if time.time() - starttime > timeout:
                            return

                    r = mlen + readbuffer[(r - 1)]
                    while len(readbuffer) < r:
                        tempbuf = self.dev._read()
                        length = len(tempbuf)
                        i = 0
                        if length > 2:
                            while i < length:
                                readbuffer += tempbuf[i + 2:i + 64]
                                i += 64

                        if time.time() - starttime > timeout:
                            return

                    return readbuffer[mlen:].tostring()
                return
            except USBError as e:
                try:
                    if e.errno == 2:
                        self.dev.stats['usb_busy'] += 1
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

    def send_command(self, mtype, data=None):
        retries = self.dev.retries
        msg, ml, dl = format_message(mtype, data if data is not None else [])
        r = 0
        ret = None
        while r <= retries:
            dispatcher.send(signal='ecu.debug', sender=self, msg=('%d > [%s]' % (r, ', '.join(['%02x' % m for m in msg]))))
            resp = self.send(msg, ml)
            if resp:
                if checksum8bitHonda(resp[:-1]) == resp[(-1)]:
                    dispatcher.send(signal='ecu.debug', sender=self, msg=('%d < [%s]' % (r, ', '.join(['%02x' % r for r in resp]))))
                    rmtype = resp[:ml]
                    valid = False
                    if ml == 3:
                        valid = rmtype[:2] == bytearray(map(lambda x: x | 16, mtype[:2]))
                elif ml == 2:
                    valid = [b for b in rmtype] == mtype
                else:
                    if ml == 1:
                        valid = rmtype == bytearray(map(lambda x: x & 15, mtype))
                    elif valid:
                        rml = resp[ml:ml + 1]
                        rdl = ord(rml) - 2 - len(rmtype)
                        rdata = resp[ml + 1:-1]
                        ret = (rmtype, rml, rdata, rdl)
                        break
                    else:
                        self.dev.stats['checksum_errors'] += 1
                        continue
            r += 1
            self.dev.stats['retries'] += 1

        dispatcher.send(signal='ecu.stats', sender=self, data=(self.dev.stats))
        return ret

    def ping(self, mode=114):
        return self.send_command([254], [mode]) is not None

    def diag(self, mode=240):
        return self.send_command([114], [0, mode]) is not None

    def detect_ecu_state(self):
        _retries = self.dev.retries
        self.dev.retries = 0
        state = ECUSTATE.OFF
        if self.dev.kline():
            state = ECUSTATE.UNKNOWN
            self.init()
            self.init()
            self.ping()
            t0 = self.send_command([114], [113, 0])
            if t0 is not None:
                if bytes(t0[2][5:7]) == b'\x00\x00':
                    d3 = self.send_command([125], [1, 1, 3])
                    if d3 is not None:
                        state = ECUSTATE.RECOVER_OLD
                    else:
                        b4 = self.send_command([123], [0, 1, 4])
                        if b4 is not None:
                            state = ECUSTATE.RECOVER_NEW
            else:
                state = ECUSTATE.OK
        else:
            w0 = self.send_command([126], [1, 1, 0])
            if w0 is not None:
                state = ECUSTATE.FLASH
            else:
                s10 = self.send_command([130, 130, 16], [0])
                if s10 is not None:
                    state = ECUSTATE.SECURE
                self.dev.retries = _retries
                return state

    def probe_tables(self, tables=None):
        _retries = self.dev.retries
        self.dev.retries = 0
        if not tables:
            tables = [
             16, 17, 19, 23, 32, 33, 96, 97, 99, 103, 112, 113, 208, 209]
        ret = {}
        for t in tables:
            info = self.send_command([114], [113, t])
            if info:
                if info[3] > 2:
                    ret[t] = [
                     info[3], info[2]]
            else:
                ret = {}
                break

        self.dev.retries = _retries
        return ret

    def do_init_recover(self):
        self.send_command([123], [0, 2, 118, 3, 23])
        self.send_command([123], [0, 3, 117, 5, 19])

    def do_init_write(self):
        self.send_command([125], [1, 2, 80, 71, 77])
        self.send_command([125], [1, 3, 45, 70, 73])

    def get_write_status(self):
        status = None
        info = self.send_command([126], [1, 1, 0])
        if info:
            status = info[2][1]
        return status

    def do_erase(self):
        ret = False
        self.send_command([126], [1, 2])
        self.send_command([126], [1, 3, 0, 0])
        self.get_write_status()
        self.send_command([126], [1, 11, 0, 0, 0, 255, 255, 255])
        self.get_write_status()
        self.send_command([126], [1, 14, 1, 144])
        time.sleep(0.04)
        info = self.send_command([126], [1, 4, 255])
        if info:
            if info[2][1] == 0:
                ret = True
        return ret

    def do_erase_wait(self):
        cont = 1
        while cont:
            time.sleep(0.1)
            info = self.send_command([126], [1, 5])
            if info:
                if info[2][1] == 0:
                    cont = 0
            else:
                cont = -1

        if cont == 0:
            self.get_write_status()

    def do_post_write(self):
        ret = False
        self.send_command([126], [1, 8])
        time.sleep(0.5)
        self.get_write_status()
        self.send_command([126], [1, 9])
        time.sleep(0.5)
        self.get_write_status()
        self.send_command([126], [1, 10])
        time.sleep(0.5)
        self.get_write_status()
        self.send_command([126], [1, 12])
        time.sleep(0.5)
        if self.get_write_status() == 15:
            info = self.send_command([126], [1, 13])
            if info:
                ret = info[2][1] == 15
        return ret

    def get_faults(self):
        faults = {'past':[],  'current':[]}
        for i in range(1, 12):
            info_current = self.send_command([114], [116, i])[2]
            for j in (3, 5, 7):
                if info_current[j] != 0:
                    faults['current'].append('%02d-%02d' % (info_current[j], info_current[(j + 1)]))

            if info_current[2] == 0:
                break

        for i in range(1, 12):
            info_past = self.send_command([114], [115, i])[2]
            for j in (3, 5, 7):
                if info_past[j] != 0:
                    faults['past'].append('%02d-%02d' % (info_past[j], info_past[(j + 1)]))

            if info_past[2] == 0:
                break

        return faults

    def pgmfi_read_flash_bytes(self, location, size=12):
        if size <= 12:
            info = self.send_command([130, 130, 0], format_read(location) + [size])
            if info:
                if struct.unpack('<B', info[1])[0] == size + 5:
                    return (
                     True, info[2])
        return (False, None)

    def pgmfi_read_ram_bytes(self, location, size=12):
        if size <= 12:
            info = self.send_command([130, 130, 4], list(struct.unpack('<2B', struct.pack('<H', location))) + [size])
            if info:
                if struct.unpack('<B', info[1])[0] == size + 5:
                    return (
                     True, struct.unpack('<%sB' % size, info[2]))
        return (False, None)

    def pgmfi_read_ram_words(self, location, size=6):
        size2 = size * 2
        if size % 2 == 0:
            if size <= 6:
                info = self.send_command([130, 130, 5], list(struct.unpack('<2B', struct.pack('<H', location))) + [size])
                if info:
                    if struct.unpack('<B', info[1])[0] == size2 + 5:
                        return (
                         True,
                         struct.unpack('<%sB' % size2, (struct.pack)('<%sH' % size, *struct.unpack('>%sH' % size, info[2]))))
        return (False, None)

    def pgmfi_write_ram_bytes(self, location, data):
        size = len(data)
        if size <= 12:
            info = self.send_command([130, 130, 8], list(struct.unpack('<2B', struct.pack('<H', location))) + list(struct.unpack('<%sB' % size, data)) + [size])
            if info:
                if struct.unpack('<B', info[1])[0] == 5:
                    return (
                     True, info[2])
        return (False, None)

    def pgmfi_write_ram_words(self, location, data):
        size = len(data)
        if size % 2 == 0:
            if size / 2 <= 6:
                info = self.send_command([130, 130, 9], list(struct.unpack('<2B', struct.pack('<H', location))) + list(struct.unpack('<%sB' % size, data)) + [size])
                if info:
                    if struct.unpack('<B', info[1])[0] == 5:
                        return (
                         True, info[2])
        return (False, None)

    def pgmfi_read_eeprom_word(self, location):
        info = self.send_command([130, 130, 16], [location])
        if info:
            if struct.unpack('<B', info[1])[0] == 7:
                return (
                 True, struct.unpack('<2B', info[2]))
        return (False, None)

    def pgmfi_write_eeprom_word(self, location, data):
        info = self.send_command([130, 130, 20], [location] + list(struct.unpack('<2B', data)))
        if info:
            if struct.unpack('<B', info[1])[0] == 5:
                return (
                 True, info[2])
        return (False, None)

    def pgmfi_format_eeprom_FF(self):
        info = self.send_command([130, 130, 24])
        if info:
            if struct.unpack('<B', info[1])[0] == 5:
                return (
                 True, info[2])
        return (False, None)

    def pgmfi_format_eeprom_00(self):
        info = self.send_command([130, 130, 25])
        if info:
            if struct.unpack('<B', info[1])[0] == 5:
                return (
                 True, info[2])
        return (False, None)

    def pgmfi_write_unk1_byte(self, location, data):
        size = len(data)
        if size <= 12:
            info = self.send_command([130, 130, 29], list(struct.unpack('<2B', struct.pack('<H', location))) + list(struct.unpack('<%sB' % size, data)))
            if info:
                if struct.unpack('<B', info[1])[0] == 5:
                    return (
                     True, info[2])
        return (False, None)

    def pgmfi_write_unk1_word(self, location, data):
        size = len(data)
        if size % 2 == 0:
            if size / 2 <= 6:
                info = self.send_command([130, 130, 30], list(struct.unpack('<2B', struct.pack('<H', location))) + list(struct.unpack('<%sB' % size, data)))
                if info:
                    if struct.unpack('<B', info[1])[0] == 5:
                        return (
                         True, info[2])
        return (False, None)
