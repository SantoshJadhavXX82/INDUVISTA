"""Unit tests for app.workers.enron_channel.

The parser is the heart of Phase 9.1.1. It accepts Daniel SIM 2251's
non-standard Enron Modbus responses (byte_count = N × width + trailing)
while still catching structural corruption. These tests exercise:

  * Every (fc, width, trailing) combination on the happy path
  * Slave exceptions (Modbus exception responses)
  * Every malformation: truncated MBAP, wrong txn id, wrong protocol id,
    wrong unit id, wrong FC, truncated PDU, byte_count < required,
    byte_count > 250, empty data, etc.
  * The build_enron_request round-trip
  * EnronChannel lifecycle: connect, request, reconnect, close
  * Concurrent request serialization
  * Backoff on connect failure

Channel tests use an in-process asyncio TCP server so no docker or
external simulator is needed. Tests run in <2 seconds.
"""
from __future__ import annotations

import asyncio
import struct
import pytest
import pytest_asyncio

from app.workers.enron_channel import (
    EnronChannel,
    EnronConnectError,
    EnronError,
    EnronProtocolError,
    EnronSlaveException,
    EnronTimeoutError,
    MODBUS_EXCEPTION_NAMES,
    build_enron_request,
    parse_enron_response,
)


pytestmark = pytest.mark.unit


# ===========================================================================
# Helper — synthesize Enron-style responses
# ===========================================================================

def make_response(
    txn_id: int = 1,
    protocol_id: int = 0,
    unit_id: int = 1,
    fc: int = 3,
    byte_count: int | None = None,
    data: bytes = b"",
    extra_trailing: bytes = b"",
) -> bytes:
    """Build a Modbus TCP response frame for testing.

    If byte_count is None, it defaults to len(data) (standard Modbus).
    For Enron-style responses, set byte_count = len(data) + len(extra_trailing)
    and pass the trailing bytes via extra_trailing — the resulting frame
    has the Daniel-style "extra padding" pymodbus rejects.
    """
    payload = data + extra_trailing
    bc = byte_count if byte_count is not None else len(payload)
    pdu = bytes([fc, bc]) + payload
    length = 1 + len(pdu)  # unit_id + PDU
    mbap = struct.pack(">HHHB", txn_id, protocol_id, length, unit_id)
    return mbap + pdu


def make_exception_response(
    txn_id: int = 1,
    unit_id: int = 1,
    fc: int = 3,
    exception_code: int = 2,
) -> bytes:
    """Build a Modbus exception response (FC has high bit set)."""
    pdu = bytes([fc | 0x80, exception_code])
    length = 1 + len(pdu)
    mbap = struct.pack(">HHHB", txn_id, 0, length, unit_id)
    return mbap + pdu


def encode_floats_abcd(values: list[float]) -> bytes:
    """Encode a list of float32 values as big-endian ABCD bytes."""
    return b"".join(struct.pack(">f", v) for v in values)


# ===========================================================================
# build_enron_request — pure function tests
# ===========================================================================

class TestBuildEnronRequest:
    def test_fc03_canonical(self):
        """FC03 read at 7001 count=16, txn=17, unit=1 — exactly what the
        Daniel test program in the user screenshot produced."""
        req = build_enron_request(
            transaction_id=0x0011, unit_id=1, function_code=3,
            start_address=7001, count=16,
        )
        # Match against the test program's TX bytes exactly
        # 00 11 00 00 00 06 01 03 1B 59 00 10
        assert req == bytes.fromhex("0011000000060103 1B59 0010".replace(" ", ""))

    def test_fc03_seven_values(self):
        """The other screenshot — count=7 at 7001."""
        req = build_enron_request(
            transaction_id=0x0011, unit_id=1, function_code=3,
            start_address=7001, count=7,
        )
        assert req == bytes.fromhex("00110000000601031B590007")

    def test_fc04_supported(self):
        req = build_enron_request(
            transaction_id=42, unit_id=1, function_code=4,
            start_address=0, count=1,
        )
        # Just verify FC byte is 0x04
        assert req[7] == 0x04

    def test_length_always_12(self):
        for fc in (3, 4):
            for start in (0, 7001, 0xFFFF):
                for count in (1, 50, 125):
                    req = build_enron_request(
                        transaction_id=1, unit_id=1,
                        function_code=fc,
                        start_address=start, count=count,
                    )
                    assert len(req) == 12

    @pytest.mark.parametrize("fc", [0, 1, 2, 5, 6, 15, 16, 23, 99])
    def test_invalid_fc_rejected(self, fc):
        with pytest.raises(ValueError, match="function_code"):
            build_enron_request(
                transaction_id=1, unit_id=1, function_code=fc,
                start_address=0, count=1,
            )

    @pytest.mark.parametrize("count", [0, 126, 127, 1000, -1])
    def test_invalid_count_rejected(self, count):
        with pytest.raises(ValueError, match="count"):
            build_enron_request(
                transaction_id=1, unit_id=1, function_code=3,
                start_address=0, count=count,
            )

    @pytest.mark.parametrize("addr", [-1, 65536, 100000])
    def test_invalid_start_address_rejected(self, addr):
        with pytest.raises(ValueError, match="start_address"):
            build_enron_request(
                transaction_id=1, unit_id=1, function_code=3,
                start_address=addr, count=1,
            )


# ===========================================================================
# parse_enron_response — happy paths
# ===========================================================================

class TestParseHappy:
    def test_standard_response_decodes(self):
        """A standard Modbus response (byte_count = 2N) parses cleanly
        when value_width_bytes=2 — the 'Enron flag is conceptually only'
        case for 16-bit values."""
        registers = [0x1234, 0x5678, 0xABCD]
        data = struct.pack(">3H", *registers)
        resp = make_response(fc=3, data=data)
        result = parse_enron_response(
            response=resp, expected_transaction_id=1,
            expected_unit_id=1, expected_fc=3,
            expected_count=3, value_width_bytes=2,
        )
        assert result == registers

    def test_daniel_screenshot_count_7(self):
        """The exact response from the user's Modbus Test Program screenshot:
        7 floats at 7001, byte_count = 31 = 4×7 + 3. First 3 floats decode
        to 94.9, 2.5, 0.2 (methane, ethane, propane mole-%)."""
        floats = [94.9, 2.5, 0.2, 0.03, 0.03, 0.01, 0.01]
        data = encode_floats_abcd(floats)
        trailing = b"\x00\x00\x00"  # the famous 3 trailing bytes
        resp = make_response(
            txn_id=0x0011, fc=3,
            byte_count=len(data) + len(trailing),
            data=data, extra_trailing=trailing,
        )
        result = parse_enron_response(
            response=resp, expected_transaction_id=0x0011,
            expected_unit_id=1, expected_fc=3,
            expected_count=7, value_width_bytes=4,
        )
        # Should return 14 uint16 (7 floats × 2 registers each)
        assert len(result) == 14
        # Re-pack and decode the first float — should be ~94.9
        first_float = struct.unpack(">f", struct.pack(">2H", result[0], result[1]))[0]
        assert abs(first_float - 94.9) < 0.01

    def test_daniel_real_world_16_floats(self):
        """The exact byte_count=67 scenario from the worker log: 16 floats
        with 3 trailing bytes."""
        floats = [94.9, 2.5, 0.2, 0.03, 0.03, 0.01, 0.01, 0.01,
                  1.6, 0.7, 0.01, 0.0, 0.0, 0.0, 0.0, 0.0]
        data = encode_floats_abcd(floats)
        trailing = b"\xAB\xCD\xEF"  # ANY 3 trailing bytes (we don't care)
        resp = make_response(
            fc=3, byte_count=67, data=data, extra_trailing=trailing,
        )
        result = parse_enron_response(
            response=resp, expected_transaction_id=1,
            expected_unit_id=1, expected_fc=3,
            expected_count=16, value_width_bytes=4,
        )
        assert len(result) == 32  # 16 floats × 2 registers
        # Verify all 16 floats decode correctly
        for i in range(16):
            f = struct.unpack(
                ">f", struct.pack(">2H", result[2*i], result[2*i+1]),
            )[0]
            assert abs(f - floats[i]) < 0.01

    @pytest.mark.parametrize("trailing_len", [0, 1, 2, 3, 5, 7, 10])
    def test_arbitrary_trailing_bytes_discarded(self, trailing_len):
        """The permissive parser accepts any trailing byte count, not
        just Daniel's '3'. Different firmware variants are fine."""
        floats = [3.14, 2.71, 1.41]
        data = encode_floats_abcd(floats)
        trailing = b"\xFF" * trailing_len
        resp = make_response(
            fc=3, byte_count=len(data) + trailing_len,
            data=data, extra_trailing=trailing,
        )
        result = parse_enron_response(
            response=resp, expected_transaction_id=1,
            expected_unit_id=1, expected_fc=3,
            expected_count=3, value_width_bytes=4,
        )
        assert len(result) == 6  # 3 floats × 2

    def test_fc04_supported(self):
        """FC04 (Read Input Registers) works the same as FC03."""
        floats = [1.0, 2.0]
        data = encode_floats_abcd(floats)
        resp = make_response(fc=4, data=data)
        result = parse_enron_response(
            response=resp, expected_transaction_id=1,
            expected_unit_id=1, expected_fc=4,
            expected_count=2, value_width_bytes=4,
        )
        assert len(result) == 4

    def test_width_8_byte_double(self):
        """float64 / int64 / uint64 — width=8."""
        doubles = [1.123456789012345, 2.234567890123456]
        data = b"".join(struct.pack(">d", v) for v in doubles)
        resp = make_response(fc=3, data=data)
        result = parse_enron_response(
            response=resp, expected_transaction_id=1,
            expected_unit_id=1, expected_fc=3,
            expected_count=2, value_width_bytes=8,
        )
        assert len(result) == 8  # 2 doubles × 4 registers each

    def test_width_2_uint16_range(self):
        """The Daniel 3xxx range — uint16 values, width=2.
        Enron flag is conceptually-only here; wire-level behavior matches
        standard Modbus."""
        values = [3001, 3002, 3003, 3004, 3005]  # arbitrary uint16s
        data = struct.pack(">5H", *values)
        resp = make_response(fc=3, data=data)
        result = parse_enron_response(
            response=resp, expected_transaction_id=1,
            expected_unit_id=1, expected_fc=3,
            expected_count=5, value_width_bytes=2,
        )
        assert result == values

    @pytest.mark.parametrize("fc,width", [
        (3, 2), (3, 4), (3, 8),
        (4, 2), (4, 4), (4, 8),
    ])
    def test_matrix_fc_width(self, fc, width):
        """Every (fc, width) combination on the happy path."""
        n_values = 5
        n_bytes = n_values * width
        data = bytes(range(n_bytes))  # arbitrary deterministic pattern
        resp = make_response(fc=fc, data=data)
        result = parse_enron_response(
            response=resp, expected_transaction_id=1,
            expected_unit_id=1, expected_fc=fc,
            expected_count=n_values, value_width_bytes=width,
        )
        assert len(result) == n_bytes // 2


# ===========================================================================
# parse_enron_response — error paths
# ===========================================================================

class TestParseErrors:
    def test_invalid_width_rejected(self):
        for bad_width in (0, 1, 3, 5, 6, 7, 16):
            with pytest.raises(ValueError, match="value_width_bytes"):
                parse_enron_response(
                    response=b"\x00" * 20, expected_transaction_id=1,
                    expected_unit_id=1, expected_fc=3,
                    expected_count=1, value_width_bytes=bad_width,
                )

    def test_truncated_response(self):
        with pytest.raises(EnronProtocolError, match="too short"):
            parse_enron_response(
                response=b"\x00\x01", expected_transaction_id=1,
                expected_unit_id=1, expected_fc=3,
                expected_count=1, value_width_bytes=2,
            )

    def test_transaction_id_mismatch(self):
        """Defense against interleaved responses (which would be a real
        gateway/network issue, but worth catching)."""
        resp = make_response(txn_id=42, data=b"\x00" * 4)
        with pytest.raises(EnronProtocolError, match="transaction id"):
            parse_enron_response(
                response=resp, expected_transaction_id=43,
                expected_unit_id=1, expected_fc=3,
                expected_count=2, value_width_bytes=2,
            )

    def test_protocol_id_nonzero(self):
        """protocol_id != 0 means we're talking to an RTU-over-TCP gateway
        as if it were Modbus TCP. Clear error message."""
        resp = make_response(protocol_id=1, data=b"\x00" * 4)
        with pytest.raises(EnronProtocolError, match="protocol id|RTU-over-TCP"):
            parse_enron_response(
                response=resp, expected_transaction_id=1,
                expected_unit_id=1, expected_fc=3,
                expected_count=2, value_width_bytes=2,
            )

    def test_unit_id_mismatch(self):
        resp = make_response(unit_id=2, data=b"\x00" * 4)
        with pytest.raises(EnronProtocolError, match="unit id"):
            parse_enron_response(
                response=resp, expected_transaction_id=1,
                expected_unit_id=1, expected_fc=3,
                expected_count=2, value_width_bytes=2,
            )

    def test_fc_mismatch(self):
        resp = make_response(fc=3, data=b"\x00" * 4)
        with pytest.raises(EnronProtocolError, match="FC mismatch"):
            parse_enron_response(
                response=resp, expected_transaction_id=1,
                expected_unit_id=1, expected_fc=4,
                expected_count=2, value_width_bytes=2,
            )

    def test_byte_count_insufficient(self):
        """Device claimed fewer bytes than the requested N values."""
        resp = make_response(fc=3, byte_count=4, data=b"\x00" * 4)
        # Asked for 5 uint16 = 10 bytes, got byte_count=4
        with pytest.raises(EnronProtocolError, match="byte_count|less than"):
            parse_enron_response(
                response=resp, expected_transaction_id=1,
                expected_unit_id=1, expected_fc=3,
                expected_count=5, value_width_bytes=2,
            )

    def test_byte_count_exceeds_modbus_max(self):
        """byte_count > 250 is structurally suspect (the field is 1 byte
        but a real PDU can't exceed 253 = 256 - MBAP overhead). Reject
        cleanly rather than slicing past the buffer."""
        # Crafted with byte_count = 251, then explicitly set to fail validation
        data = b"\x00" * 251
        resp = make_response(fc=3, byte_count=251, data=data)
        with pytest.raises(EnronProtocolError, match="exceeds Modbus max"):
            parse_enron_response(
                response=resp, expected_transaction_id=1,
                expected_unit_id=1, expected_fc=3,
                expected_count=10, value_width_bytes=2,
            )

    def test_slave_exception_illegal_data_address(self):
        """When the device returns Modbus exception 2 (ILLEGAL_DATA_ADDRESS),
        we get a clean typed exception with the code and friendly name."""
        resp = make_exception_response(fc=3, exception_code=2)
        with pytest.raises(EnronSlaveException) as exc_info:
            parse_enron_response(
                response=resp, expected_transaction_id=1,
                expected_unit_id=1, expected_fc=3,
                expected_count=1, value_width_bytes=2,
            )
        assert exc_info.value.exception_code == 2
        assert "ILLEGAL_DATA_ADDRESS" in str(exc_info.value)

    def test_slave_exception_slave_busy(self):
        resp = make_exception_response(fc=3, exception_code=6)
        with pytest.raises(EnronSlaveException) as exc_info:
            parse_enron_response(
                response=resp, expected_transaction_id=1,
                expected_unit_id=1, expected_fc=3,
                expected_count=1, value_width_bytes=2,
            )
        assert exc_info.value.exception_code == 6
        assert "SLAVE_DEVICE_BUSY" in str(exc_info.value)

    def test_all_modbus_exception_codes_named(self):
        """Every exception code has a friendly name in the message."""
        for code, name in MODBUS_EXCEPTION_NAMES.items():
            resp = make_exception_response(fc=3, exception_code=code)
            with pytest.raises(EnronSlaveException) as exc_info:
                parse_enron_response(
                    response=resp, expected_transaction_id=1,
                    expected_unit_id=1, expected_fc=3,
                    expected_count=1, value_width_bytes=2,
                )
            assert name in str(exc_info.value)

    def test_unknown_exception_code_handled(self):
        """An undocumented exception code still raises cleanly."""
        resp = make_exception_response(fc=3, exception_code=99)
        with pytest.raises(EnronSlaveException) as exc_info:
            parse_enron_response(
                response=resp, expected_transaction_id=1,
                expected_unit_id=1, expected_fc=3,
                expected_count=1, value_width_bytes=2,
            )
        assert exc_info.value.exception_code == 99
        assert "EXCEPTION_99" in str(exc_info.value)


# ===========================================================================
# EnronChannel — async lifecycle tests with in-process fake server
# ===========================================================================

class FakeEnronServer:
    """Minimal asyncio TCP server that responds to Modbus requests with
    configurable Enron-style framing.

    Used by the channel tests so we have a real socket end-to-end without
    pymodbus or docker.
    """

    def __init__(
        self,
        responder=None,  # callable(req_bytes) -> resp_bytes
        connect_delay_s: float = 0.0,
    ):
        self.host = "127.0.0.1"
        self.port = 0  # OS-assigned
        self.server: asyncio.base_events.Server | None = None
        self.received_requests: list[bytes] = []
        self.responder = responder or self._default_responder
        self.connect_delay_s = connect_delay_s
        self.connection_count = 0

    async def _default_responder(self, req: bytes) -> bytes:
        """Default: echo the request's txn_id, return 16 zero bytes."""
        txn_id, _, _, unit_id, fc, _, count = struct.unpack(">HHHBBHH", req)
        data = b"\x00" * (count * 4)
        trailing = b"\x00\x00\x00"
        return make_response(
            txn_id=txn_id, unit_id=unit_id, fc=fc,
            byte_count=len(data) + len(trailing),
            data=data, extra_trailing=trailing,
        )

    async def start(self):
        if self.connect_delay_s > 0:
            await asyncio.sleep(self.connect_delay_s)
        self.server = await asyncio.start_server(
            self._handle, self.host, 0,  # 0 = OS picks port
        )
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader, writer):
        self.connection_count += 1
        try:
            while True:
                # MBAP is exactly 7 bytes, then read length-1 more for the PDU
                header = await reader.readexactly(7)
                _, _, length, _ = struct.unpack(">HHHB", header)
                pdu = await reader.readexactly(length - 1)
                req = header + pdu
                self.received_requests.append(req)
                resp = await self.responder(req)
                writer.write(resp)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


@pytest_asyncio.fixture
async def fake_server():
    """Start a FakeEnronServer for the duration of one test."""
    server = FakeEnronServer()
    await server.start()
    yield server
    await server.stop()


@pytest.mark.asyncio
class TestEnronChannelLifecycle:
    async def test_first_read_connects(self, fake_server):
        """First read opens the TCP connection lazily."""
        ch = EnronChannel(fake_server.host, fake_server.port)
        result = await ch.read_enron(
            unit_id=1, function_code=3,
            start_address=0, count=2,
            value_width_bytes=4,
        )
        assert len(result) == 4  # 2 floats × 2 registers
        assert fake_server.connection_count == 1
        await ch.close()

    async def test_subsequent_reads_reuse_socket(self, fake_server):
        """Second and third reads do NOT open new sockets — the same
        persistent connection is reused. This is the Phase 9.1.1
        risk-2 mitigation."""
        ch = EnronChannel(fake_server.host, fake_server.port)
        for _ in range(5):
            await ch.read_enron(
                unit_id=1, function_code=3,
                start_address=0, count=2,
                value_width_bytes=4,
            )
        assert fake_server.connection_count == 1
        assert len(fake_server.received_requests) == 5
        await ch.close()

    async def test_transaction_ids_increment(self, fake_server):
        """Each request uses a fresh txn id (wraps at 0xFFFF)."""
        ch = EnronChannel(fake_server.host, fake_server.port)
        for _ in range(10):
            await ch.read_enron(
                unit_id=1, function_code=3,
                start_address=0, count=1, value_width_bytes=4,
            )
        txn_ids = [
            struct.unpack(">H", req[:2])[0]
            for req in fake_server.received_requests
        ]
        # All distinct, all in order
        assert txn_ids == list(range(1, 11))
        await ch.close()

    async def test_close_releases_socket(self, fake_server):
        ch = EnronChannel(fake_server.host, fake_server.port)
        await ch.read_enron(
            unit_id=1, function_code=3,
            start_address=0, count=1, value_width_bytes=4,
        )
        stats = ch.stats()
        assert stats.connected is True
        await ch.close()
        stats = ch.stats()
        assert stats.connected is False

    async def test_close_is_idempotent(self, fake_server):
        ch = EnronChannel(fake_server.host, fake_server.port)
        await ch.close()
        await ch.close()  # must not raise


@pytest.mark.asyncio
class TestEnronChannelErrors:
    async def test_connect_refused(self):
        """Port that nothing's listening on → EnronConnectError."""
        ch = EnronChannel("127.0.0.1", 1)  # port 1 reserved, refused
        with pytest.raises(EnronConnectError):
            await ch.read_enron(
                unit_id=1, function_code=3,
                start_address=0, count=1, value_width_bytes=4,
            )

    async def test_connect_timeout(self):
        """Routable but non-responsive host → timeout error."""
        # 192.0.2.0/24 is the IETF TEST-NET-1 block — guaranteed unroutable
        ch = EnronChannel("192.0.2.1", 502)
        with pytest.raises(EnronConnectError):
            await ch.read_enron(
                unit_id=1, function_code=3,
                start_address=0, count=1, value_width_bytes=4,
                request_timeout_s=0.5,
            )

    async def test_backoff_after_failure(self):
        """After a connect failure, the next attempt is gated by backoff."""
        ch = EnronChannel(
            "127.0.0.1", 1,  # refused
            reconnect_initial_ms=500,
            reconnect_max_ms=10000,
        )
        # First attempt fails
        with pytest.raises(EnronConnectError):
            await ch.read_enron(1, 3, 0, 1, 4)
        # Immediate retry should also fail, but with an "in backoff" message
        with pytest.raises(EnronConnectError, match="backoff"):
            await ch.read_enron(1, 3, 0, 1, 4)

    async def test_slave_exception_propagates(self):
        """When the device returns a Modbus exception, the channel
        surfaces it as EnronSlaveException (not a generic error)."""
        async def exception_responder(req: bytes) -> bytes:
            txn_id = struct.unpack(">H", req[:2])[0]
            return make_exception_response(
                txn_id=txn_id, fc=3, exception_code=2,
            )

        server = FakeEnronServer(responder=exception_responder)
        await server.start()
        try:
            ch = EnronChannel(server.host, server.port)
            with pytest.raises(EnronSlaveException) as exc_info:
                await ch.read_enron(
                    unit_id=1, function_code=3,
                    start_address=99999 & 0xFFFF, count=1,
                    value_width_bytes=4,
                )
            assert exc_info.value.exception_code == 2
            await ch.close()
        finally:
            await server.stop()


@pytest.mark.asyncio
class TestEnronChannelConcurrency:
    async def test_concurrent_reads_serialize(self, fake_server):
        """Two coroutines both calling read_enron at the same time —
        the lock serializes them, both succeed, no race."""
        ch = EnronChannel(fake_server.host, fake_server.port)
        results = await asyncio.gather(*[
            ch.read_enron(1, 3, 0, 2, 4) for _ in range(20)
        ])
        assert len(results) == 20
        assert all(len(r) == 4 for r in results)
        assert len(fake_server.received_requests) == 20
        await ch.close()


@pytest.mark.asyncio
class TestEnronChannelStats:
    async def test_initial_stats(self):
        ch = EnronChannel("127.0.0.1", 9999)
        s = ch.stats()
        assert s.connected is False
        assert s.reconnect_count == 0
        assert s.request_count == 0
        assert s.failed_request_count == 0
        assert s.last_reconnect_ts is None

    async def test_stats_after_successful_reads(self, fake_server):
        ch = EnronChannel(fake_server.host, fake_server.port)
        for _ in range(3):
            await ch.read_enron(1, 3, 0, 1, 4)
        s = ch.stats()
        assert s.connected is True
        assert s.reconnect_count == 1  # one initial connect
        assert s.request_count == 3
        assert s.failed_request_count == 0
        assert s.last_reconnect_ts is not None
        await ch.close()

    async def test_stats_after_failed_connect(self):
        ch = EnronChannel("127.0.0.1", 1)  # refused
        try:
            await ch.read_enron(1, 3, 0, 1, 4)
        except EnronConnectError:
            pass
        s = ch.stats()
        assert s.connected is False
        assert s.last_error is not None
        # The failed-request counter is incremented for failed reads,
        # not for connect attempts that never got to the read step.


# ===========================================================================
# Daniel SIM 2251 — end-to-end fiscal data round-trip
# ===========================================================================

@pytest.mark.asyncio
class TestDanielSIM2251Roundtrip:
    """Exercise the parser against the EXACT byte patterns the user's
    Daniel GC produced. Sanity-check that fiscal-grade gas composition
    values come through correctly."""

    async def test_full_16_component_fiscal_response(self, fake_server):
        """Mirror the production-observed 67-byte response: 16 mole-%
        floats with 3 trailing bytes (Daniel firmware quirk)."""
        # Component mole percentages from the actual SVJ Daniel GC
        components = [
            ("Methane",     94.90),
            ("Ethane",       2.50),
            ("Propane",      0.20),
            ("i-Butane",     0.03),
            ("n-Butane",     0.03),
            ("neo-Pentane",  0.01),
            ("i-Pentane",    0.01),
            ("n-Pentane",    0.01),
            ("Hexane+",      1.60),
            ("Nitrogen",     0.70),
            ("(slot11)",     0.01),
            ("(unused12)",   0.00),
            ("(unused13)",   0.00),
            ("(unused14)",   0.00),
            ("(unused15)",   0.00),
            ("(unused16)",   0.00),
        ]
        values = [v for _, v in components]
        total = sum(values)
        assert abs(total - 100.0) < 0.05  # fiscal sanity

        async def daniel_responder(req: bytes) -> bytes:
            txn_id = struct.unpack(">H", req[:2])[0]
            data = encode_floats_abcd(values)
            trailing = b"\x00\x00\x00"  # Daniel's signature 3-byte trailer
            return make_response(
                txn_id=txn_id, unit_id=1, fc=3,
                byte_count=len(data) + len(trailing),
                data=data, extra_trailing=trailing,
            )

        server = FakeEnronServer(responder=daniel_responder)
        await server.start()
        try:
            ch = EnronChannel(server.host, server.port)
            result = await ch.read_enron(
                unit_id=1, function_code=3,
                start_address=7001, count=16,
                value_width_bytes=4,
            )
            # 16 floats × 2 uint16 registers = 32 entries
            assert len(result) == 32
            # Decode each float and verify
            for i, (name, expected) in enumerate(components):
                packed = struct.pack(">2H", result[2*i], result[2*i+1])
                actual = struct.unpack(">f", packed)[0]
                assert abs(actual - expected) < 0.01, (
                    f"component '{name}' mismatch: "
                    f"expected {expected}, got {actual}"
                )
            await ch.close()
        finally:
            await server.stop()
