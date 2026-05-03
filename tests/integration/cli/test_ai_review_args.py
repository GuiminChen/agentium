from agentium.cli.commands.ai_review import build_parser


def test_ai_review_parser_accepts_required_args() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["--pr-number", "1", "--repo", "owner/repo", "--author", "alice"]
    )
    assert args.pr_number == 1
    assert args.repo == "owner/repo"
    assert args.author == "alice"
