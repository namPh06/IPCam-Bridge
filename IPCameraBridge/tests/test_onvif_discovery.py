import unittest

from ip_camera_bridge.onvif_discovery import parse_probe_matches


class OnvifDiscoveryTests(unittest.TestCase):
    def test_parses_name_and_builds_editable_rtsp_url(self):
        response = b'''<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
          xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery">
          <e:Body><d:ProbeMatches><d:ProbeMatch>
            <d:Scopes>onvif://www.onvif.org/name/Front%20Door</d:Scopes>
            <d:XAddrs>http://192.168.10.25/onvif/device_service</d:XAddrs>
          </d:ProbeMatch></d:ProbeMatches></e:Body></e:Envelope>'''
        devices = parse_probe_matches(response)
        self.assertEqual([(item.name, item.host, item.rtsp_url) for item in devices],
                         [('Front Door', '192.168.10.25', 'rtsp://192.168.10.25:554/')])
        self.assertEqual(parse_probe_matches(b'<not-xml'), [])


if __name__ == '__main__':
    unittest.main()
