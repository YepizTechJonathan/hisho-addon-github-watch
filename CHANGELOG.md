# Changelog

## 0.1.2

- Every field on its setup form says in plain words what it is, and links to where to get it (addon.yaml `help:`, shown by Hisho's dashboard).

## 0.1.1

- README says what you need (addon.yaml you_need) and how to use it; the README check requires both.

## 0.1.0

- First version: a repository's PRs (opened, merged, closed), CI results and deploy workflow results as short
  lines for team-chat channels through `chat_messages`; failures also to one alerts channel. GET calls only;
  `post: false` (default) only journals and lists the lines on the page. First look only remembers.
