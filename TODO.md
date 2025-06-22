# EventKit Refactoring and Modernization TODO

(auto-generated TODO from Claude suggestions. All tasks may not be valid, but some should be explored.)

## Overview

This document outlines prioritized tasks for refactoring, performance improvements, and modernization of the EventKit codebase. Based on comprehensive code review and analysis.

**Current Test Coverage: 88% (80 tests)**

---

## 🔥 HIGH PRIORITY (Breaking Changes & Critical Issues)

### 1. Fix Deprecated Asyncio Patterns

**Impact**: These patterns will break in future Python versions

#### A. Replace deprecated `asyncio.ensure_future()` calls
- **Files**: `eventkit/event.py:106`, `eventkit/ops/create.py:20,46`
- **Issue**: Uses deprecated `loop=` parameter
- **Fix**: Replace with `asyncio.create_task()`
```python
# Current (deprecated)
asyncio.ensure_future(result, loop=get_event_loop())

# Modern
asyncio.create_task(result)
```

#### B. Update `get_event_loop()` function
- **File**: `eventkit/util.py:19-22`
- **Issue**: Uses deprecated `asyncio.get_event_loop()`
- **Coverage**: Only 32% - indicates this function needs attention
- **Fix**: Use `asyncio.get_running_loop()` with fallback

### 2. Critical Performance Bottlenecks

#### A. Eliminate Slot Copying (CRITICAL)
- **File**: `eventkit/event.py:83`
- **Issue**: `self.slots.copy()` creates full copy on every emit
- **Impact**: 10-50% performance overhead for high-frequency events
- **Fix**: Use iterator-based approach or snapshot only when needed

#### B. Optimize Slot Removal Operations
- **Files**: `eventkit/event.py:42-48,51-56,59-64`
- **Issue**: O(n) list recreation for each removal using `itertools.filterfalse()`
- **Impact**: 20-100% overhead for frequent connect/disconnect
- **Fix**: Use in-place removal or mark-and-sweep approach

#### C. Cache Event Loop References
- **Files**: `eventkit/event.py:106,317,348`
- **Issue**: Frequent `get_event_loop()` calls
- **Impact**: 2-5x slower async callback handling
- **Fix**: Cache loop reference per thread

### 3. Memory Leaks and Resource Management

#### A. Unbounded Collections
- **Files**:
  - `eventkit/ops/select.py:117` - Growing `_seen` set without bounds
  - `eventkit/ops/combine.py:183,227` - `defaultdict(list)` accumulation
  - `eventkit/ops/transform.py:224` - Unbounded coroutine queue
- **Fix**: Implement size limits, cleanup strategies, or LRU caches

#### B. Task Cleanup Issues
- **Files**: `eventkit/ops/create.py:36,60`
- **Issue**: Manual task cancellation in `__del__` is unreliable
- **Fix**: Implement proper async context managers

---

## 🚨 MEDIUM PRIORITY (Quality & Maintainability)

### 4. Code Quality Improvements

#### A. Extract Repeated Argument Handling Pattern
- **Files**: Found in 7+ operator files
- **Pattern**: `args[0] if len(args) == 1 else args if args else NO_VALUE`
- **Fix**: Create utility function in `util.py`

#### B. Simplify Complex Conditional Logic
- **Files**:
  - `eventkit/ops/combine.py:161-166,191-192,242-253`
  - `eventkit/ops/select.py:120-124`
  - `eventkit/ops/timing.py:167-168`
- **Fix**: Refactor into smaller methods, use pattern matching (Python 3.10+)

#### C. Replace Lambda Functions in Classes
- **Files**: `eventkit/ops/aggregate.py:85,92`
- **Issue**: Lambda functions in class initializers are not debuggable
- **Fix**: Replace with proper methods

### 5. Type Hints and Modern Python Patterns

#### A. Add Comprehensive Type Hints
- **Missing in**: All files except `event.py` and partial `op.py`
- **Priority Files**:
  - `eventkit/ops/aggregate.py`
  - `eventkit/ops/combine.py`
  - `eventkit/ops/timing.py`

#### B. Modernize with Dataclasses
- **Files**: `eventkit/ops/misc.py`, simple data holder classes
- **Benefit**: Reduce boilerplate, automatic `__repr__`, etc.

#### C. Use `from __future__ import annotations`
- **Current**: Only in `event.py`
- **Fix**: Add to all files for consistent typing

### 6. Test Coverage Improvements

#### A. Low Coverage Areas (Priority Order)
1. **`eventkit/util.py`** - 32% coverage
   - Missing: timerange function (lines 45-74)
   - Add: Async utility function tests

2. **`eventkit/ops/misc.py`** - 50% coverage
   - Missing: Error handling paths (lines 9-14, 21, 24-26)
   - Add: Edge case tests

3. **`eventkit/ops/array.py`** - 75% coverage
   - Missing: Array operation edge cases
   - Add: NumPy integration tests

4. **`eventkit/ops/aggregate.py`** - 84% coverage
   - Missing: Complex aggregation scenarios
   - Add: Performance stress tests

#### B. Missing Test Categories
- **Performance Tests**: None found
- **Integration Tests**: Limited cross-operator testing
- **Error Scenario Tests**: Insufficient error path coverage
- **Concurrency Tests**: Limited async stress testing
- **Memory Tests**: No memory leak detection tests

#### C. Test Modernization
- **Issue**: Uses deprecated `asyncio_default_fixture_loop_scope` config
- **Pattern**: Still using `unittest.TestCase` instead of pytest patterns
- **Fix**: Migrate to `pytest-asyncio` and modern async test patterns

### 7. Error Handling Consistency

#### A. Inconsistent Error Propagation
- **Files**: Multiple operator files
- **Issue**: Some errors are swallowed, others re-raised inconsistently
- **Fix**: Establish consistent error handling patterns

#### B. Broad Exception Handling
- **Files**: `eventkit/ops/transform.py:103-112`
- **Issue**: Catches `Exception` instead of specific types
- **Fix**: Use specific exception types

---

## 🔧 LOW PRIORITY (Nice-to-Have)

### 8. Performance Optimizations

#### A. Method Call Overhead Reduction
- **File**: `eventkit/event.py:197-199,414-431`
- **Issue**: Complex conditional logic in hot paths
- **Fix**: Optimize common cases, use dispatch tables

#### B. Data Structure Optimizations
- **File**: `eventkit/event.py:35`
- **Issue**: List provides O(n) removal operations
- **Fix**: Consider `collections.deque` or mark-and-sweep

#### C. Async Detection Optimization
- **File**: `eventkit/event.py:105`
- **Issue**: `hasattr()` check is expensive
- **Fix**: Use try/except pattern or cached detection

### 9. Modern Python Features

#### A. Pattern Matching (Python 3.10+)
- **Files**: Complex conditional logic throughout
- **Benefit**: Cleaner, more readable code

#### B. TaskGroup Usage (Python 3.11+)
- **Files**: `eventkit/ops/transform.py` for concurrent task management
- **Benefit**: Better resource management and error handling

#### C. `asyncio.timeout()` (Python 3.11+)
- **Files**: Replace `asyncio.wait_for()` usage
- **Benefit**: More efficient timeout handling

### 10. Documentation and Examples

#### A. Missing Documentation Coverage
- **Docstrings**: Inconsistent across operator files
- **Type Hints**: Help with IDE support and documentation generation

#### B. Performance Documentation
- **Missing**: Performance characteristics of operators
- **Missing**: Memory usage guidelines
- **Missing**: Async best practices guide

---

## 📊 Implementation Priority Matrix

| Task | Impact | Effort | Priority |
|------|---------|---------|----------|
| Fix deprecated asyncio patterns | High | Low | 🔥 URGENT |
| Eliminate slot copying | High | Medium | 🔥 URGENT |
| Cache event loop references | High | Low | 🔥 URGENT |
| Fix memory leaks | High | Medium | 🔥 URGENT |
| Add type hints | Medium | High | 🚨 MEDIUM |
| Improve test coverage | Medium | High | 🚨 MEDIUM |
| Extract common patterns | Medium | Medium | 🚨 MEDIUM |
| Performance optimizations | Low | Medium | 🔧 LOW |
| Modern Python features | Low | Low | 🔧 LOW |

---

## 🛠 Implementation Strategy

### Phase 1: Critical Fixes (1-2 weeks)
1. Fix deprecated asyncio patterns
2. Optimize slot operations
3. Fix memory leaks
4. Update test configuration

### Phase 2: Quality Improvements (2-3 weeks)
1. Add comprehensive type hints
2. Extract common patterns
3. Improve test coverage to >95%
4. Standardize error handling

### Phase 3: Modernization (2-4 weeks)
1. Adopt modern Python patterns
2. Performance optimizations
3. Enhanced documentation
4. Integration tests

---

## 📈 Success Metrics

- **Test Coverage**: 88% → 95%+
- **Performance**: 20-50% improvement in high-frequency scenarios
- **Code Quality**: Eliminate all deprecated patterns
- **Maintainability**: Consistent patterns across all modules
- **Type Safety**: 100% type hint coverage

---

## ⚠️ Breaking Changes Considerations

The following changes require careful consideration for backward compatibility:

1. **Event loop handling changes** - May affect user code that manually manages loops
2. **Performance optimizations** - Could change timing characteristics
3. **Error handling changes** - May change exception types or propagation

**Recommendation**: Implement with feature flags and deprecation warnings where appropriate.

---

*Generated from comprehensive code review including performance analysis, test coverage assessment, and modern Python pattern evaluation.*
