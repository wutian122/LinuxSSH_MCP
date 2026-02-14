from linux_ssh_mcp.token_optimizer import TokenOptimizer


def test_filter_by_pattern_keeps_matching_lines() -> None:
    optimizer = TokenOptimizer()
    text = "ok\nerror: bad\nwarn: meh\nerror: worse"
    assert optimizer.filter_by_pattern(text, pattern=r"^error:") == "error: bad\nerror: worse"


def test_estimate_tokens_counts_cjk_and_others() -> None:
    optimizer = TokenOptimizer()
    assert optimizer.estimate_tokens("你好") >= 2
    assert optimizer.estimate_tokens("abcd") >= 1


def test_truncate_by_tokens_reduces_text() -> None:
    optimizer = TokenOptimizer()
    text = "a" * 1000
    truncated = optimizer.truncate_by_tokens(text, max_tokens=10)
    assert truncated != text
    assert optimizer.estimate_tokens(truncated) <= 10

