# Changelog

## 0.2.0

- Your repositories are listed on the page ("Found", from this machine's GitHub sign-in, newest first, ones already
  watched left out): Use this fills in the repository. Quiet when there is no sign-in.
- The token stays optional and can now be pasted (kept as a local secret); a secret's name still works. Test says
  which sign-in it used, and when neither works, what to do (sign in once, or paste a read-only token).

## 0.1.2

- Every field on its setup form says in plain words what it is, and links to where to get it (addon.yaml `help:`, shown by Hisho's dashboard).

## 0.1.1

- README says what you need (addon.yaml you_need) and how to use it; the README check requires both.

## 0.1.0

- First version: a repository's PRs (opened, merged, closed), CI results and deploy workflow results as short
  lines for team-chat channels through `chat_messages`; failures also to one alerts channel. GET calls only;
  `post: false` (default) only journals and lists the lines on the page. First look only remembers.
