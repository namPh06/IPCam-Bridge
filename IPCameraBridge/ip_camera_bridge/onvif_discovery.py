"""Small ONVIF WS-Discovery client for finding cameras on the local LAN."""
from dataclasses import dataclass
import socket
import time
import select
from concurrent.futures import ThreadPoolExecutor
from ipaddress import ip_address, ip_network
from urllib.parse import unquote, urlsplit
from uuid import uuid4
from xml.etree import ElementTree


MULTICAST = ('239.255.255.250', 3702)


@dataclass(frozen=True)
class OnvifDevice:
    name: str
    host: str
    xaddr: str

    @property
    def rtsp_url(self):
        host = f'[{self.host}]' if ':' in self.host else self.host
        return f'rtsp://{host}:554/'


def _text(element, name):
    child = element.find(f'.//{{*}}{name}')
    return child.text.strip() if child is not None and child.text else ''


def _scope_name(scopes, host):
    for scope in scopes.split():
        marker = '/name/'
        if marker in scope:
            value = unquote(scope.split(marker, 1)[1]).replace('+', ' ').strip()
            value = ''.join(char for char in value if char.isprintable())[:128]
            if value:
                return value
    return f'Camera {host}'


def parse_probe_matches(data):
    """Parse an untrusted UDP response without contacting the advertised URL."""
    try:
        root = ElementTree.fromstring(data)
    except (ElementTree.ParseError, ValueError):
        return []
    devices = []
    for match in root.findall('.//{*}ProbeMatch'):
        scopes = _text(match, 'Scopes')
        for xaddr in _text(match, 'XAddrs').split():
            try:
                parts = urlsplit(xaddr)
                if parts.scheme not in ('http', 'https') or not parts.hostname:
                    continue
                host = parts.hostname
            except ValueError:
                continue
            devices.append(OnvifDevice(_scope_name(scopes, host), host, xaddr[:2048]))
            break
    return devices


def local_interfaces():
    """Use active IPv4 interfaces, including explicit choices for VPN/VM adapters."""
    from PySide6.QtNetwork import QNetworkInterface
    result = []
    flags = QNetworkInterface.InterfaceFlag
    for interface in QNetworkInterface.allInterfaces():
        if not interface.flags() & flags.IsUp or not interface.flags() & flags.IsRunning:
            continue
        for entry in interface.addressEntries():
            try:
                address = ip_address(entry.ip().toString())
                if address.version != 4 or address.is_loopback or address.is_link_local:
                    continue
                network = str(ip_network(f'{address}/{entry.prefixLength()}', strict=False))
                result.append((interface.humanReadableName(), str(address), network))
            except ValueError:
                continue
    return result


def discover_onvif(timeout=4.0, interfaces=None, cancel=None):
    message_id = f'uuid:{uuid4()}'
    probe = f'''<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <e:Header><w:MessageID>{message_id}</w:MessageID>
  <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
  <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
 </e:Header>
 <e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>
</e:Envelope>'''.encode('utf-8')
    found = {}
    sockets = []
    try:
        for address in (interfaces if interfaces is not None else [item[1] for item in local_interfaces()]) or ['0.0.0.0']:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            try:
                sock.bind((address, 0))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(address))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
                for _ in range(2):
                    sock.sendto(probe, MULTICAST)
                sockets.append(sock)
            except OSError:
                sock.close()
        if not sockets:
            raise OSError('No usable discovery interface')
        deadline = time.monotonic() + max(.1, min(float(timeout), 15.0))
        while not (cancel and cancel.is_set()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            ready, _, _ = select.select(sockets, [], [], min(.2, remaining))
            for sock in ready:
                try:
                    data, _ = sock.recvfrom(65535)
                except OSError:
                    continue
                for device in parse_probe_matches(data):
                    found.setdefault(device.host.casefold(), device)
    finally:
        for sock in sockets:
            sock.close()
    return sorted(found.values(), key=lambda device: (device.name.casefold(), device.host))


def scan_network(value):
    network = ip_network(value.strip(), strict=False)
    if network.version != 4 or network.num_addresses > 1024:
        raise ValueError('Nhập dải IPv4 tối đa 1024 địa chỉ, ví dụ 192.168.100.0/24.')
    if network.is_multicast or network.is_loopback or network.is_unspecified or network.is_link_local:
        raise ValueError('Chọn dải mạng camera hợp lệ.')
    return network


def scan_range(start, end):
    """Validate inclusive IPv4 bounds before making any network requests."""
    first, last = ip_address(start.strip()), ip_address(end.strip())
    if first.version != 4 or last.version != 4 or not 1 <= int(last) - int(first) + 1 <= 1024:
        raise ValueError('Nhập IP bắt đầu và kết thúc theo thứ tự, tối đa 1024 địa chỉ IPv4.')
    hosts = [ip_address(value) for value in range(int(first), int(last) + 1)]
    if any(host.is_multicast or host.is_loopback or host.is_unspecified or host.is_link_local
           or host.is_reserved for host in hosts):
        raise ValueError('Dải IP chứa địa chỉ không dùng cho camera. Hãy chọn dải mạng hợp lệ.')
    return hosts


def scan_rtsp(network, cancel, progress=None, port=554, *, end=None):
    """Find RTSP responders, not merely open ports. No credentials are sent."""
    hosts = scan_range(network, end) if end is not None else list(scan_network(network).hosts())
    def probe(host):
        if cancel.is_set():
            return None
        try:
            with socket.create_connection((str(host), port), timeout=.5) as sock:
                sock.settimeout(.5)
                sock.sendall(b'OPTIONS * RTSP/1.0\r\nCSeq: 1\r\n\r\n')
                response = b''
                while len(response) < 16 and b'\r\n' not in response:
                    chunk = sock.recv(128)
                    if not chunk:
                        break
                    response += chunk
                if response.startswith(b'RTSP/1.0 '):
                    return OnvifDevice(f'RTSP {host} (chưa xác nhận model)', str(host), '')
        except OSError:
            pass
        return None
    found = []
    with ThreadPoolExecutor(max_workers=32) as pool:
        for count, device in enumerate(pool.map(probe, hosts), 1):
            if device:
                found.append(device)
            if progress:
                progress(count, len(hosts))
            if cancel.is_set():
                break
    return found
