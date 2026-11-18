from factory.github import Comment, Reaction
from factory.watch.thread import IssueThread

SELF_ID = 4242


class RecordingGitHub:
    def __init__(self, comments=(), reactions=()):
        self.log = []
        self._comments = list(comments)
        self._reactions = list(reactions)

    def comments(self, issue_number):
        self.log.append(("comments", issue_number))
        return self._comments

    def reactions(self, comment_id):
        self.log.append(("reactions", comment_id))
        return self._reactions

    def comment(self, issue_number, body):
        self.log.append(("comment", issue_number, body))
        return 99


def thread_on(github, number=5):
    return IssueThread(github, number, SELF_ID)


def test_each_method_makes_one_call_for_its_issue():
    reaction = Reaction("+1", 1, "alice")
    github = RecordingGitHub(comments=[Comment(7, "hi", 1, "alice")], reactions=[reaction])
    thread = thread_on(github)

    assert thread.comments() == [Comment(7, "hi", 1, "alice")]
    assert thread.reactions(7) == [reaction]
    assert thread.comment("hello") == 99

    assert github.log == [("comments", 5), ("reactions", 7), ("comment", 5, "hello")]


def test_own_comment_id_adopts_this_watchers_matching_comment():
    github = RecordingGitHub(comments=[Comment(7, "mine", SELF_ID, "factory-bot")])

    assert thread_on(github).own_comment_id(lambda c: c.body == "mine") == 7
    assert github.log == [("comments", 5)]


def test_own_comment_id_ignores_a_same_text_comment_from_another_account():
    github = RecordingGitHub(comments=[Comment(7, "mine", 1, "factory-bot")])

    assert thread_on(github).own_comment_id(lambda c: c.body == "mine") is None


def test_own_comment_id_is_none_when_nothing_matches():
    github = RecordingGitHub(comments=[Comment(7, "other", SELF_ID, "factory-bot")])

    assert thread_on(github).own_comment_id(lambda c: c.body == "mine") is None
