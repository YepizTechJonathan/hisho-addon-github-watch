# Github Watch (Hisho add-on)

## What it does

Watches one GitHub repository per entry and turns what changed into short posts for your team chat:

- pull requests opened, merged or closed, and their CI result (`pr_channel`);
- each deploy workflow's result in its own channel (`deploys`);
- every CI or deploy failure in one channel (`alerts_channel`).

It reads GitHub with GET calls only (no comments, reviews, merges or re-runs) and never looks at what a deploy deploys to. Posts go out through the chat add-on (Discord) only when the entry says `post: true`; until then they are journaled and listed on its page as "would post".

## What it needs

- You need:
  - A GitHub repository you can read (this machine's GitHub sign-in from Hisho's setup, or a read-only fine-grained token)
  - A chat add-on that provides `chat_messages` (the Discord add-on)
- Runs on: the Core machine, inside Hisho Core
- An add-on that provides: `chat_messages`
- One entry per repository, each with:
  - `repo` (str, required)
  - `token` (password, kept as a local secret, optional)
  - `pr_channel` (str, optional)
  - `ci_workflow` (str, optional)
  - `deploys` (str, optional)
  - `alerts_channel` (str, optional)
  - `post` (bool, optional)
  - `chat_server` (str, optional)
  - `note` (str, optional)

## How to install

On the dashboard: **Add-ons > Store > Github Watch > Install**. Or from a terminal:

```bash
hisho addon install github-watch
```

Hisho takes the newest version from this repository, copies it to the machine it runs on and turns
it on. Updates show as **Update available** on its Store card; **Roll back** returns to the version
before.

## How to use

Add a repository on its page (**Add-ons > GitHub watch**) with the channels to post to. When the Core machine is
signed in to GitHub (Hisho's setup wizard, or `gh auth login`), the page lists your repositories that aren't watched
yet: **Use this** fills in the repository, and the token can stay empty (Hisho reads with that sign-in, GET calls
only). Without that sign-in, paste a fine-grained read-only token for the repository (Pull requests, Actions,
Metadata). Until the entry says `post: true`, each line is only listed on the page as **would post**, so you can check the wording first; then turn posting on.

## Settings

On its page (**Add-ons > Github Watch**), or in `~/Hisho/System/Config/Addons/github-watch.yaml`. A token pasted
on the page is kept as a local secret on the Core machine; in the file, secrets are given by name, never by value.

## How to remove

**Add-ons > Store > Github Watch > Remove**, or `hisho addon remove github-watch`. Your settings file stays,
so installing it again picks up where you left off.

## License

Source-available under the [PolyForm Shield License 1.0.0](LICENSE.md): free to use, change and share,
for any purpose except offering a product that competes with Hisho (reselling it, for example).
