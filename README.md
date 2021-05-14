# Covid19 Vaccine India Assist Telegram Bot

Extended from [avinassh/cowin-assist](https://github.com/avinassh/cowin-assist). Go check it out.

This is a simple Telegram bot to

- Check slots availability
- Get an alert when slots become available

## Note

On 6th May 2021, CoWin API added caching and rate limits. The public API data would be cached upto 30 minutes, so the alerts wouldn't be so instant in busy areas, which reduced this bot's functionality to being a nice UI for public CoWin site in Telegram.

## Installation and Deployment

Following section helps you host the bot on your own servers. 

### Prerequisites

You need a bot account on Telegram. Use [@BotFather](https://t.me/BotFather) to create one. If you are new to Telegram Bots, you may start from here [Bots: An introduction for developers](https://core.telegram.org/bots).

### System Requirements

- Docker
- Docker Compose

### Running

Get the code and update your data in `.env`:

```shell
git clone https://github.com/karambir/covid19-assist.git
cd covid19-assist
cp example.env .env
```

Then run it using docker

```shell
docker push karambir/covid19-assist:latest

docker-compose up
```

### Running without Docker

Install the project requirements from `requirements.txt`:

```shell
$ pip install -r requirements.txt
```

Rename `sample_secrets.py` to `secrets.py` and fill it with appropriate details. Then you can run:

```shell
$ python main.py

2021-05-06 09:59:29,238 - __main__ - INFO - starting a bg worker - frequent_background_worker
2021-05-06 09:59:29,239 - __main__ - INFO - starting a bg worker - periodic_background_worker
2021-05-06 09:59:29,239 - apscheduler.scheduler - INFO - Scheduler started
```

## Development

Open an issue for any discussions and feel free to send a PR.

## Disclaimer

Not affiliated with Ministry of Health and Family Welfare OR Government of India in any capacity.

## License

Released under MIT License. Check `LICENSE` file more info.
