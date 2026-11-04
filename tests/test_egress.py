"""The proxy's decisions, without a network."""
import pytest

from egress import proxy

ALLOW = {"tailspin-offers.test"}


def resolver(*addresses):
    return lambda host, port: list(addresses)


def decide(host, port=80, addresses=("93.184.216.34",), allow=ALLOW, denied=()):
    return proxy.decide(host, port, allow, set(denied), resolver(*addresses))


def test_public_address_is_allowed():
    assert decide("example.com") == ("93.184.216.34", None)


@pytest.mark.parametrize("address,reason", [
    ("169.254.169.254", "cloud metadata address"),
    ("10.0.0.1", "private or local address"),
    ("172.18.0.2", "private or local address"),
    ("192.168.1.1", "private or local address"),
    ("127.0.0.1", "private or local address"),
    ("100.64.0.1", "private or local address"),
    ("169.254.1.1", "private or local address"),
    ("0.0.0.0", "private or local address"),
    ("::1", "private or local address"),
    ("fd00::1", "private or local address"),
    ("fe80::1%eth0", "private or local address"),
    ("::ffff:169.254.169.254", "cloud metadata address"),
    ("::ffff:10.0.0.1", "private or local address"),
])
def test_addresses_off_the_public_internet_are_blocked(address, reason):
    assert decide("rebind.example", addresses=(address,)) == (None, reason)


def test_a_name_with_one_private_address_is_blocked():
    assert decide("mixed.example", addresses=("93.184.216.34", "10.0.0.1"))[0] is None


def test_the_checked_address_is_the_one_used():
    address, _ = decide("example.com", addresses=("93.184.216.34",))
    assert address == "93.184.216.34"


@pytest.mark.parametrize("host", ["app", "launcher", "egress", "host.docker.internal", "metadata.google.internal",
                                  "LAUNCHER", "launcher."])
def test_internal_names_are_blocked_whatever_they_resolve_to(host):
    assert decide(host) == (None, "denied host")


def test_exact_test_hostname_may_be_private():
    assert decide("tailspin-offers.test", addresses=("172.20.0.3",)) == ("172.20.0.3", None)


def test_allowance_is_exact():
    assert decide("x.tailspin-offers.test", addresses=("172.20.0.3",))[0] is None


def test_a_test_hostname_gets_no_other_port():
    assert decide("tailspin-offers.test", 8100, ("172.20.0.3",)) == (None, "port not allowed")


@pytest.mark.parametrize("port", [22, 2375, 3128, 8000, 8100])
def test_only_web_ports(port):
    assert decide("example.com", port) == (None, "port not allowed")


def test_deny_list_covers_the_vm_public_address():
    assert decide("vm.example", addresses=("93.184.216.34",), denied=("93.184.216.34",)) == (None, "denied address")


def test_name_that_does_not_resolve():
    def fail(host, port):
        raise OSError

    assert proxy.decide("nope.example", 80, ALLOW, set(), fail) == (None, "name did not resolve")


def test_request_targets():
    assert proxy.target_of("GET", "http://a.test/x?y=1") == ("http", "a.test", 80, "/x?y=1")
    assert proxy.target_of("CONNECT", "a.test:443") == ("https", "a.test", 443, None)
    with pytest.raises(ValueError):
        proxy.target_of("GET", "/relative")
    with pytest.raises(ValueError):
        proxy.target_of("GET", "ftp://a.test/")


def test_forwarded_head_drops_proxy_headers_and_closes():
    head = proxy.forwarded_head("GET", "/x", ["Host: a.test", "Proxy-Connection: keep-alive",
                                              "Proxy-Authorization: x", "Connection: keep-alive"]).decode()
    assert head == "GET /x HTTP/1.1\r\nHost: a.test\r\nConnection: close\r\n\r\n"
