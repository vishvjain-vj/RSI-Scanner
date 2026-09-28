# Real-Time NSE RSI Scanner

A real-time stock market monitoring system that streams NSE market data from Angel One SmartAPI, converts live ticks into OHLCV candles, calculates technical indicators such as RSI, and exposes the results through a REST API and web dashboard.

The project focuses on real-time data processing, API integration, WebSocket streaming, concurrency, caching, error handling, and deployment.

---

## Features

* Real-time NSE market-data streaming
* Angel One SmartAPI integration
* Persistent WebSocket connection for live ticks
* Instrument master lookup for symbol-to-token resolution
* Historical candle loading
* Tick-to-OHLCV candle aggregation
* Wilder's RSI calculation
* Multiple timeframe support
* REST API for scanner management
* Real-time dashboard
* API rate-limit handling
* Retry and exponential backoff
* WebSocket reconnect handling
* In-memory state management with bounded buffers
* Dockerized deployment
* Health-check endpoint

---

## Architecture

```text
                         ┌──────────────────────┐
                         │     Angel One API    │
                         └──────────┬───────────┘
                                    │
                    ┌───────────────┴───────────────┐
                    │                               │
             Historical API                    WebSocket
                    │                               │
                    ▼                               ▼
             Historical Data                   Live Ticks
                    │                               │
                    └───────────────┬───────────────┘
                                    ▼
                             ┌─────────────┐
                             │ CandleStore │
                             └──────┬──────┘
                                    │
                                    ▼
                             ┌─────────────┐
                             │ RSI Engine  │
                             └──────┬──────┘
                                    │
                                    ▼
                             ┌─────────────┐
                             │ Flask REST  │
                             │     API     │
                             └──────┬──────┘
                                    │
                                    ▼
                             ┌─────────────┐
                             │ Web Dashboard│
                             └─────────────┘
```

---

## How It Works

### 1. Authentication

The application authenticates with Angel One SmartAPI using the required credentials and TOTP-based authentication.

Sensitive credentials are stored using environment variables rather than being hard-coded into the application.

```text
Credentials
     ↓
TOTP Authentication
     ↓
SmartAPI Session
```

---

## 2. Instrument Master

Angel One identifies tradable instruments using broker-specific instrument tokens.

The application therefore maintains an instrument master mapping between human-readable symbols and broker tokens.

```text
User Input
"RELIANCE"
     ↓
Instrument Master
     ↓
RELIANCE-EQ
     ↓
Instrument Token
     ↓
WebSocket Subscription
```

The instrument mapping is cached so that the application does not need to repeatedly download and process the complete instrument list.

This allows the scanner to efficiently resolve tickers before establishing live subscriptions.

---

## 3. Historical Data

Historical candle data is loaded when required to initialize the scanner.

This is important because technical indicators such as RSI require sufficient historical prices before a valid value can be calculated.

The historical-data pipeline:

```text
Historical API
      ↓
Fetch data in chunks
      ↓
Normalize timestamps
      ↓
Concatenate
      ↓
Sort chronologically
      ↓
Remove duplicates
      ↓
Validate OHLC data
      ↓
CandleStore
```

Request chunking and delays are used to work within API limitations.

---

## 4. Live Market Data

After the required instruments are resolved, the application establishes a WebSocket connection with Angel One.

The WebSocket continuously receives market ticks.

```text
Angel One WebSocket
        ↓
     Live Tick
        ↓
  Candle Aggregation
        ↓
     OHLCV Candle
```

WebSocket is used instead of repeatedly polling the REST API because market data is continuously changing and a streaming connection is more suitable for real-time updates.

---

## 5. Tick-to-Candle Aggregation

Individual market ticks are converted into candles according to the selected timeframe.

For example:

```text
10:00 → ₹100
10:01 → ₹101
10:02 → ₹99
10:04 → ₹103
```

The resulting candle could be:

```text
Open  = ₹100
High  = ₹103
Low   = ₹99
Close = ₹103
```

`CandleStore` maintains this evolving candle state and updates it as new ticks arrive.

The system supports multiple timeframes such as:

```text
1m
5m
15m
30m
1h
1D
```

---

## 6. RSI Calculation

The scanner calculates RSI using **Wilder's smoothing method**.

The implementation follows the RSI calculation rather than simply treating RSI as a black-box library function.

Conceptually:

```text
Price Data
    ↓
Price Changes
    ↓
Gains / Losses
    ↓
Smoothed Average Gain/Loss
    ↓
Relative Strength
    ↓
RSI
```

RSI is used as a technical indicator for monitoring market conditions. It is not treated as a guaranteed prediction of future price movement.

---

## 7. Concurrency

The application performs several activities concurrently:

```text
Flask API
    │
    ├── WebSocket processing
    │
    ├── Historical data loading
    │
    └── Dashboard/API requests
```

Python threading is used for I/O-oriented background operations.

Because multiple threads can access shared market state, locks are used around critical sections to prevent race conditions.

For example:

```text
WebSocket Thread
      │
      │ updates CandleStore
      ▼
 Shared State
      ▲
      │ reads
      │
Flask Thread
```

Without proper synchronization, one thread could read partially updated state while another thread is modifying it.

---

## 8. API Layer

Flask provides REST endpoints for interacting with the scanner.

Examples include:

```text
/health
/search_ticker
/add_ticker
/remove_ticker
/set_timeframe
/get_dashboard
```

REST APIs are used for request-response operations such as managing tracked stocks and retrieving scanner state.

WebSocket is used separately for continuous market-data streaming.

---

## 9. Frontend

The dashboard uses:

* HTML
* CSS
* JavaScript

The frontend displays the current scanner state and RSI information in a heatmap-style interface.

Ticker search uses debouncing to avoid unnecessary API requests while the user is typing.

For example:

```text
User types:

R
RE
REL
RELIANCE

Instead of making 4 API calls,
the application waits briefly and sends
the final search request.
```

---

## 10. Reliability

The project handles failures from external APIs and network connections.

### Retry and Backoff

When an API request fails temporarily:

```text
Request
  ↓
Failure
  ↓
Wait
  ↓
Retry
  ↓
Failure
  ↓
Longer wait
  ↓
Retry
```

Exponential backoff reduces the chance of continuously hitting an unavailable or rate-limited service.

### WebSocket Reconnection

If the live connection is interrupted, the application can detect the failure and attempt to restore the connection.

### Timeouts

Network operations use timeouts so that the application does not remain blocked indefinitely.

---

## 11. Caching

Caching is used at multiple levels.

### Instrument Mapping

```text
Symbol → Token
```

is cached to avoid repeatedly processing the instrument master.

### Session Information

Authentication/session information can be cached where appropriate.

### Market State

Frequently accessed real-time candle/indicator state is maintained in memory for fast access.

---

## 12. Memory Management

The scanner continuously receives live market data, so storing every tick indefinitely would cause memory usage to grow.

The application therefore maintains bounded data structures for the required candle history.

```text
Unlimited ticks
      ↓
Memory growth
      ↓
Problem

Bounded candle buffer
      ↓
Controlled memory usage
```

---

## 13. Deployment

The application is containerized using Docker.

Docker packages the application together with its runtime dependencies so that it can be deployed consistently.

The deployment setup also uses:

* Gunicorn for production Flask serving
* Environment-based secrets
* Health checks
* Persistent storage where required
* Production-oriented container configuration

Docker is used primarily to make deployment reproducible and isolated.

---

# Technology Stack

| Technology          | Purpose                    |
| ------------------- | -------------------------- |
| Python              | Core application           |
| Angel One SmartAPI  | Market data                |
| WebSocket           | Real-time streaming        |
| Flask               | REST API/backend           |
| Pandas              | Historical data processing |
| NumPy               | Numerical operations       |
| HTML/CSS/JavaScript | Dashboard                  |
| Docker              | Containerization           |
| Gunicorn            | Production WSGI server     |
| TOTP / PyOTP        | Authentication             |
| Git/GitHub          | Version control            |

---

# Project Structure

```text
RSI-Scanner/
│
├── main.py
├── config.py
├── smartapi_loader.py
├── websocket_manager.py
├── candle_store.py
├── indicators.py
│
├── heatmap.html
│
├── Dockerfile
├── fly.toml
├── requirements.txt
│
└── data/
```

### Main Components

**`main.py`**

Application orchestration and Flask API.

**`config.py`**

Configuration and environment-based settings.

**`smartapi_loader.py`**

Angel One authentication, instrument handling and historical data loading.

**`websocket_manager.py`**

Live WebSocket connection and tick processing.

**`candle_store.py`**

Maintains candle state and converts ticks into OHLCV candles.

**`indicators.py`**

Technical-indicator calculations including RSI.

**`heatmap.html`**

Frontend dashboard.

---

# Challenges Solved

### 1. Real-time data processing

Solved by using WebSocket streaming and a candle aggregation layer.

### 2. Instrument identification

Solved through instrument-master symbol-to-token mapping and caching.

### 3. API rate limits

Handled using request chunking, delays, retries and exponential backoff.

### 4. WebSocket failures

Handled through connection monitoring and reconnection logic.

### 5. Race conditions

Shared state is protected using locks when accessed concurrently.

### 6. Historical/live data overlap

Historical data is normalized, sorted and deduplicated before being used.

### 7. Memory growth

A bounded candle buffer prevents unlimited accumulation of market data.

---

# Future Improvements

The current system provides the core real-time scanning pipeline. Possible future improvements include:

### More Technical Indicators

Build a modular indicator architecture supporting:

```text
RSI
EMA
MACD
ATR
Bollinger Bands
```

without changing the core market-data pipeline.

### Persistent Database

Store historical candles and generated signals in MySQL for:

* historical analysis
* backtesting
* signal tracking
* long-term storage

### Redis

Explore Redis for low-latency shared state and caching if the application is scaled across multiple backend instances.

### Message Queue

Learn and experiment with Kafka/message queues to decouple:

```text
Market Data Ingestion
        ↓
Message Broker
        ↓
Processing Workers
```

This would make the architecture more suitable for higher-volume event processing.

### Observability

Add metrics for:

* WebSocket connection status
* API latency
* processing latency
* error rates
* reconnect frequency

### Automated Testing

Add unit tests for critical components such as:

* RSI calculation
* candle aggregation
* timeframe boundaries
* instrument-token resolution

---

# What I Learned

Through this project I learned how to build a real-time system rather than just implement an individual algorithm.

Key areas I worked with:

* REST APIs
* WebSocket communication
* TOTP authentication
* Instrument-token mapping
* Historical data processing
* Tick-to-candle aggregation
* Technical-indicator calculation
* Python threading
* Locks and race-condition prevention
* API rate limiting
* Retry and exponential backoff
* Caching
* Error handling
* Logging and health checks
* Docker-based deployment
* Frontend/backend integration

---

# Future Direction

The long-term goal is to evolve the project from a single real-time scanner into a more scalable financial-data processing platform.

The planned progression is:

```text
Current
Real-time WebSocket Scanner
        ↓
Modular Indicators
        ↓
Persistent Historical Storage
        ↓
Automated Testing
        ↓
Observability
        ↓
Redis / Shared State
        ↓
Event-driven Processing
        ↓
Scalable Real-Time Architecture
```

---

## Disclaimer

This project is intended for educational and software-engineering purposes. RSI and other technical indicators should not be considered guaranteed predictions of future market prices.
