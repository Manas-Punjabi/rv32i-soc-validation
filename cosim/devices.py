"""devices.py — Python models of the SoC peripherals, for running firmware on the ISS.

These mirror the RTL register maps in rv32i-soc-asic (rtl/periph/, sw/soc.h). They let
firmware run stand-alone on the ISS (`make iss`). In lockstep the RTL's observed values
replace every model read, so a model bug cannot hide a firmware bug there.
"""
from .iss import MMIO

SLAVE_STRIDE = 0x1000
UART_BASE  = 0x4000_0000
ACCEL_BASE = 0x4000_1000
TIMER_BASE = 0x4000_2000
GPIO_BASE  = 0x4000_3000


class AccelModel:
    """Register-level model of apb_mm_accel + mm_accel.

    Completes instantly: done is set by the start write itself. Latency (N+4 cycles on
    the RTL) is invisible to firmware that polls done, which is exactly the contract the
    driver relies on — so an instant model is the correct model for firmware.
    """
    def __init__(self, n: int = 4, dw: int = 32):
        self.n, self.dw = n, dw
        self.a = [0] * n * n
        self.b = [0] * n * n
        self.c = [0] * n * n
        self.done = False
        self.mask = (1 << dw) - 1

    def read(self, off: int) -> int:
        if off == 0x004:
            return int(self.done) << 1          # busy is never observed
        if off == 0x008:
            return (self.dw << 8) | self.n
        if 0xC00 <= off < 0xC00 + 4 * self.n * self.n:
            return self.c[(off - 0xC00) // 4]
        return 0

    def write(self, off: int, v: int) -> None:
        nn = self.n * self.n
        if 0x400 <= off < 0x400 + 4 * nn:
            self.a[(off - 0x400) // 4] = v & self.mask
        elif 0x800 <= off < 0x800 + 4 * nn:
            self.b[(off - 0x800) // 4] = v & self.mask
        elif off == 0x000 and v & 1:
            n = self.n
            for i in range(n):
                for j in range(n):
                    self.c[i * n + j] = sum(self.a[i * n + k] * self.b[k * n + j]
                                            for k in range(n)) & self.mask
            self.done = True
        elif off == 0x004 and v & 2:
            self.done = False


class UartModel:
    """apb_uart, TX side: collects bytes written to TXDATA; never busy, never receives."""
    TX_READY, TX_IDLE = 0x01, 0x02

    def __init__(self):
        self.out = bytearray()
        self.ctrl = 0
        self.div = 868                       # soc_top's UART_DIV default

    def read(self, off: int) -> int:
        if off == 0x008:                     # STATUS: TX always ready and idle, no RX
            return self.TX_READY | self.TX_IDLE
        if off == 0x00C:
            return self.ctrl
        if off == 0x010:
            return self.div
        return 0                             # TXDATA is write-only; RXDATA: nothing received

    def write(self, off: int, v: int) -> None:
        if off == 0x000:
            self.out.append(v & 0xFF)
        elif off == 0x00C:
            self.ctrl = v & 0x3
        elif off == 0x010:
            self.div = v & 0xFFFF


class TimerModel:
    """CLINT-style timer (apb_timer). mtime comes from `clock()`, which the ISS binds to
    its retirement count — one tick per instruction, i.e. a CPI-1 machine. Firmware may
    only rely on mtime being monotonic; the RTL's real value is injected in lockstep."""
    def __init__(self, clock=lambda: 0):
        self.clock = clock
        self.offset = 0                      # mtime = clock() + offset, so writes stick
        self.mtimecmp = (1 << 64) - 1        # resets to all ones
        self.msip = 0

    def _mtime(self) -> int:
        return (self.clock() + self.offset) & ((1 << 64) - 1)

    def read(self, off: int) -> int:
        if off == 0x000: return self._mtime() & 0xFFFFFFFF
        if off == 0x004: return self._mtime() >> 32
        if off == 0x008: return self.mtimecmp & 0xFFFFFFFF
        if off == 0x00C: return self.mtimecmp >> 32
        if off == 0x010: return self.msip
        return 0

    def write(self, off: int, v: int) -> None:
        if off in (0x000, 0x004):
            t = self._mtime()
            t = (t & ~0xFFFFFFFF) | v if off == 0 else (t & 0xFFFFFFFF) | v << 32
            self.offset = t - self.clock()
        elif off == 0x008: self.mtimecmp = (self.mtimecmp & ~0xFFFFFFFF) | v
        elif off == 0x00C: self.mtimecmp = (self.mtimecmp & 0xFFFFFFFF) | v << 32
        elif off == 0x010: self.msip = v & 1


class GpioModel:
    """apb_gpio with gpio_in looped back from gpio_out, as tb_soc wires it. The RTL's
    2-flop input synchronizer delays IN by two clocks; the model doesn't."""
    def __init__(self, width: int = 8):
        self.mask = (1 << width) - 1
        self.out = 0

    def read(self, off: int) -> int:
        return self.out if off in (0x000, 0x004) else 0

    def write(self, off: int, v: int) -> None:
        if off == 0x000:   self.out = v & self.mask
        elif off == 0x008: self.out |= v & self.mask
        elif off == 0x00C: self.out &= ~v & self.mask


class SocMMIO(MMIO):
    """Routes MMIO to the device models by slave window."""
    def __init__(self, accel_n: int = 4):
        super().__init__()
        self.uart = UartModel()
        self.accel = AccelModel(n=accel_n)
        self.timer = TimerModel()
        self.gpio = GpioModel()
        self.devices = {UART_BASE: self.uart, ACCEL_BASE: self.accel,
                        TIMER_BASE: self.timer, GPIO_BASE: self.gpio}

    def bind_clock(self, clock) -> None:
        self.timer.clock = clock

    def _dev(self, addr):
        base = addr & ~(SLAVE_STRIDE - 1)
        return self.devices.get(base), addr - base

    def read(self, addr: int, nbytes: int) -> int:
        dev, off = self._dev(addr)
        return dev.read(off) if dev else 0

    def write(self, addr: int, nbytes: int, value: int) -> None:
        super().write(addr, nbytes, value)
        dev, off = self._dev(addr)
        if dev:
            dev.write(off, value)
