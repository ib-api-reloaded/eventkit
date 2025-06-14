# EventKit Architecture

## Overview

EventKit is a Python library for event-driven data pipelines, providing a comprehensive framework for creating reactive, asynchronous data processing systems. It enables loosely coupled components to communicate through events and offers a rich set of operators for building complex data flow pipelines.

## Core Architecture

### Event System

The central component is the `Event` class (`eventkit/event.py`), which implements the Observer pattern with these key features:

- **Event Emission**: Events can emit values to connected listeners using `emit(*args)`
- **Listener Management**: Connect/disconnect listeners with `connect()`/`disconnect()` or `+=`/`-=` operators
- **Weak References**: Automatic cleanup of garbage-collected listeners (unless `keep_ref=True`)
- **Error Handling**: Built-in error propagation through `error_event` sub-events
- **Completion Tracking**: Event lifecycle management with `done_event` and `set_done()`

### Slot System

Events use a sophisticated slot system (`Slots` class in `eventkit/event.py:34-117`) for managing listeners:

- **Slot Objects**: Store listener references, weak references, and function pointers
- **Callback Execution**: Efficiently iterate through and call all connected listeners
- **Automatic Cleanup**: Remove dead weak references during iteration
- **Async Support**: Automatically schedule coroutines in the event loop

### Operator Framework

The `Op` base class (`eventkit/ops/op.py`) provides the foundation for all event operators:

- **Source Management**: Operators connect to source events and process their emissions
- **Event Forwarding**: Default behavior passes through source events unchanged
- **Error Propagation**: Forwards errors from source to operator's error event
- **Lifecycle Management**: Handles source completion and cleanup

## Operator Categories

### 1. Creation Operators (`eventkit/ops/create.py`)

Generate events from various sources:

- **Timer**: Emit at regular intervals
- **Sequence**: Emit values from an iterable with optional timing
- **Range**: Emit integer sequences
- **Timerange**: Emit datetime values at specified intervals
- **Wait**: Convert Futures/awaitables to events
- **Aiterate**: Convert async iterators to events

### 2. Transformation Operators (`eventkit/ops/transform.py`)

Modify emitted values:

- **Map**: Apply functions to values (sync/async)
- **Enumerate**: Add index counters
- **Pluck**: Extract nested properties or positional arguments
- **Star/Pack**: Unpack/pack tuples
- **Constant**: Replace values with constants
- **Timestamp**: Add timestamps to values

### 3. Selection Operators (`eventkit/ops/select.py`)

Filter which values pass through:

- **Filter**: Apply predicates to values
- **Take/Skip**: Limit number of values
- **TakeWhile/DropWhile**: Conditional value passing
- **TakeUntil**: Stop on notifier event
- **Changes/Unique**: Eliminate duplicates
- **Last**: Emit only final value

### 4. Aggregation Operators (`eventkit/ops/aggregate.py`)

Accumulate values over time:

- **Sum/Product**: Mathematical accumulation
- **Min/Max/Mean**: Statistical operators
- **Count**: Track emission count
- **Reduce**: Custom accumulation functions
- **List/Deque**: Collect values in containers
- **Ema**: Exponential moving average

### 5. Array Operators (`eventkit/ops/array.py`)

NumPy integration for array operations:

- **Array**: Maintain sliding windows of values
- **ArraySum/ArrayMean/ArrayStd**: Array-specific aggregations
- **ArrayMin/ArrayMax**: Array extrema operations

### 6. Combination Operators (`eventkit/ops/combine.py`)

Merge multiple event streams:

- **Merge**: Combine events as they occur
- **Zip**: Synchronize events by position
- **ZipLatest**: Combine using latest values
- **Chain**: Sequential event processing
- **Concat**: Concatenate event streams
- **Switch**: Switch between event sources

### 7. Timing Operators (`eventkit/ops/timing.py`)

Control event timing:

- **Delay**: Time-shift events
- **Debounce**: Filter rapid successive events
- **Throttle**: Rate-limit events
- **Sample**: Sample at specific intervals
- **Timeout**: Handle event timeouts

### 8. Higher-Order Operators

Advanced event composition:

- **Emap**: Map to new events with custom joining
- **Mergemap/Concatmap/Chainmap/Switchmap**: Specialized higher-order mapping

## Async Integration

EventKit provides seamless asyncio integration:

### Event Loop Integration

- **get_event_loop()** (`eventkit/util.py:19`): Get or create event loop
- **Coroutine Handling**: Automatic scheduling of async listeners
- **Thread Safety**: `emit_threadsafe()` for cross-thread communication

### Async Interoperability

Events can be converted to/from standard Python async primitives:

- **Async Iterator**: Use `async for` with events
- **Awaitable**: Use `await` to get next emission
- **Future Integration**: Convert Futures to events and vice versa

## Data Flow Patterns

### Pipeline Construction

Multiple equivalent syntaxes for building pipelines:

```python
# Method chaining (most common)
event = source.map(func).filter(pred).take(10)

# Pipe operator
event = source | Map(func) | Filter(pred) | Take(10)

# Explicit piping
event = source.pipe(Map(func), Filter(pred), Take(10))

# Constructor chaining
event = Take(10, Filter(pred, Map(func, source)))
```

### Forking and Joining

Split event streams and recombine them:

```python
# Fork with square bracket syntax
result = source[Min, Max, Mean].zip()

# Explicit forking
fork = source.fork(Min(), Max(), Mean())
result = fork.zip()
```

### Flow Control

Implement backpressure and flow control:

```python
# Feedback loops for pacing
pacer = Event()
pipeline = pacer.iterate(data).map(process).connect(pacer.emit)
pacer.emit()  # Kickstart
```

## Memory Management

### Weak References

- Listeners are stored as weak references by default
- Automatic cleanup when objects are garbage collected
- `keep_ref=True` for strong references when needed

### Resource Cleanup

- Events properly disconnect from sources when done
- Async tasks are cancelled on event destruction
- Error events and done events are automatically cleaned up

## Performance Considerations

### Efficient Execution

- Slot iteration uses copies to handle dynamic listener changes
- Async coroutines are scheduled efficiently with `asyncio.ensure_future()`
- Weak reference cleanup is performed during slot execution

### Scalability

- No global state - events are independent
- Operators can be chained indefinitely
- Concurrent processing through async operators

## Extension Points

### Custom Operators

Create custom operators by subclassing `Op`:

```python
class CustomOp(Op):
    def on_source(self, *args):
        # Process args
        self.emit(processed_args)

    def on_source_error(self, source, error):
        # Handle errors
        pass

    def on_source_done(self, source):
        # Handle completion
        self.set_done()
```

### Event Subclassing

Extend the base `Event` class for specialized event types with custom behavior.

## Testing and Development

### Test Structure

- Comprehensive test suite in `tests/` directory
- Tests organized by operator category
- Both sync and async test patterns

### Development Tools

- **Poetry**: Dependency management and packaging
- **Ruff**: Code formatting and linting
- **MyPy**: Type checking
- **Pytest**: Testing framework with asyncio support
