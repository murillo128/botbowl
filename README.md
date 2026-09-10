# Bot Bowl

Bot Bowl is a Python framework for playing Blood Bowl and developing and
evaluating AI bots. This repository is a maintained fork of
[the original Bot Bowl project](https://github.com/njustesen/botbowl); its
history, authorship and research attribution are preserved.

![botbowl](docs/img/botbowl_webUI.png?raw=true "botbowl")

Please cite our paper if you use botbowl in your publications.
```
@inproceedings{justesen2019blood,
  title={Blood Bowl: A New Board Game Challenge and Competition for AI},
  author={Justesen, Niels and Moore, Peter David and Uth, Lasse M{\o}ller and Togelius, Julian and Jakobsen, Christopher and Risi, Sebastian}
  booktitle={2019 IEEE Conference on Games (COG)},
  year={2019},
  organization={IEEE}
}
```

Read more about the challenges and achievements in [Papers.md](docs/papers.md)

## Installation

This fork's new packaging line requires Python 3.11 or newer and pip.
Then run:
```shell
python -m pip install git+https://github.com/murillo128/botbowl
```
The default installation is headless and uses Python pathfinding without a compiler. Optional integrations and explicit native builds are described in the [installation guide](docs/installation.md). Built distributions exclude the legacy browser artwork; the existing graphical UI can be run from a checkout with its applicable permissions.

Start with one of four independent, bounded paths:

- [minimal headless core](docs/quickstart-core.md)
- [registered bots](docs/quickstart-bots.md)
- [Gymnasium v5 environment](docs/quickstart-gymnasium.md)
- [optional local web UI](docs/quickstart-web.md)

For a verified-wheel, non-root container, see the [container guide](docs/docker.md).
The default container runs a finite headless smoke; web serving is an explicit
build target.

For local dataset windows and control of both teams from a separately installed
consumer, see the [external lab consumer guide](docs/lab/external-client.md).

## Tutorials
Head over to our [tutorials](docs/tutorials.md) to learn about how to use the Bot Bowl framework.

### Run Bot Bowl's Web Server

Install the web extra and bind locally. Debug and the reloader are disabled by
the documented entry point:

```shell
python -m pip install 'botbowl[web] @ git+https://github.com/murillo128/botbowl.git'
python -m botbowl web --host 127.0.0.1 --port 1234
```

Go to: http://127.0.0.1:1234/

The main page lists active games. For each active game you can click on a team to play it. If a team is disabled it is controlled by a bot and cannot be selected. Click hot-seat to play human vs. human on the same machine.

## Bot Bowl - the competition
Bot Bowl is an AI competition using the botbowl framework. Go read all about the results of [Bot Bowl III](docs/bot-bowl-iii.md). Not to be confused with 'botbowl' which refers to the module/repo. 

## Get involved 
We'd love you to join us in creating really good bots for Blood Bowl! There are mainly two ways. First is developing your own bot to compete in the competition. Head over to the [tutorials](docs/tutorials.md) to get started creating your own awesome bot. The second way is contributing to the project itself with development. Head over to [Development.md](docs/development.md) to learn more.  

Join the [Bot Bowl Discord server](https://discord.gg/MTXMuae) for questions, discussions and the latest news! 

## Releases, compatibility and rights

The fork version is available as `botbowl.__version__`. See the
[changelog](CHANGELOG.md), [migration guide](docs/migration.md),
[support and reproducibility boundaries](docs/support.md), and
[release procedure](docs/releasing.md). Release automation only prepares
reviewable artifacts; it does not publish to PyPI or another external registry.

Bot Bowl is not affiliated with or endorsed by any company or trademark. The
Apache-2.0 [source-code license](LICENSE) is not a license for every file in the
repository. The checkout contains inherited graphics with separate or unclear
permissions; built distributions and the default container exclude them. Team
icons are described by upstream as used with FUMBBL permission, and some icons
originate from Fantasy Football Client. Do not treat those statements as a
general redistribution grant. See the separate [rights inventory](THIRD_PARTY_NOTICES.md).

For installed laboratory dataset, HTTP, snapshot and branching walkthroughs, see
the [laboratory quickstarts](docs/lab/quickstarts.md) and their
[release compatibility proposal](docs/lab/release-proposal.md).
