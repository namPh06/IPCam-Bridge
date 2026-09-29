"""Small ONVIF WS-Discovery client for finding cameras on the local LAN."""
from dataclasses import dataclass
import socket
import time
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


def discover_onvif(timeout=4.0):
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
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
        sock.bind(('', 0))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        deadline = time.monotonic() + max(.1, min(float(timeout), 15.0))
        for _ in range(2):
            sock.sendto(probe, MULTICAST)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(min(.5, remaining))
            try:
                data, _ = sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            for device in parse_probe_matches(data):
                found.setdefault(device.host.casefold(), device)
    return sorted(found.values(), key=lambda device: (device.name.casefold(), device.host))
