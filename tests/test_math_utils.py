from utils.math_utils import is_prime

def test_primes():
    assert is_prime(2)
    assert is_prime(3)
    assert not is_prime(1)
    assert not is_prime(4)
