"""Actual compressed streams remain readable under the decoded-byte limit."""
import gzip
import zlib
import httpx
import pytest
from radar.fetch.http import HttpClient

@pytest.mark.parametrize('encoding,compress', [('gzip',gzip.compress),('deflate',zlib.compress)])
def test_bounded_stream_decodes_exactly_once_and_preserves_source_headers(encoding,compress):
    body=b'<html><title>Real company source</title><p>Evidence</p></html>'
    def respond(request):
        return httpx.Response(200,headers={'content-encoding':encoding,'content-type':'text/html',
                                           'etag':'actual-source-tag'},content=compress(body))
    with HttpClient(transport=httpx.MockTransport(respond),obey_robots=False) as client:
        result=client.get('https://source.example/article',max_bytes=1000)
    assert result.text==body.decode()
    assert result.headers['etag']=='actual-source-tag'
    assert result.headers['content-type']=='text/html'


def test_compressed_body_limit_applies_to_decoded_bytes():
    body=b'x'*200_000
    closed=[]
    class Stream(httpx.SyncByteStream):
        def __iter__(self):yield gzip.compress(body)
        def close(self):closed.append(True)
    def respond(request):
        return httpx.Response(200,headers={'content-encoding':'gzip'},stream=Stream())
    with HttpClient(transport=httpx.MockTransport(respond),obey_robots=False,max_retries=0) as client:
        with pytest.raises(ValueError,match='byte limit'):
            client.get('https://source.example/article',max_bytes=1000)
    assert closed
