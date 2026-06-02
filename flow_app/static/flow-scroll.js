(function() {
  'use strict';

  var sbTweaks = { trackInset: 6, trackTop: 9, trackBottom: 9, thumbMin: 10, thumbSize: 4 };

  function initCustomScrollbar(el) {
    if (!el || (el.parentElement && el.parentElement.classList.contains('flow-scroll-wrapper'))) return;

    var isAuto = el.classList.contains('flow-scroll-auto');
    var style = getComputedStyle(el);
    var hasVertical = style.overflowY === 'auto' || style.overflowY === 'scroll';
    var hasHorizontal = style.overflowX === 'auto' || style.overflowX === 'scroll';
    if (!hasVertical && !hasHorizontal) return;

    var wrapper = document.createElement('div');
    wrapper.className = 'flow-scroll-wrapper';
    ['board-area', 'card-list', 'content', 'sidebar', 'ideas-viewport', 'table-wrap', 'output', 'flow-select-menu'].forEach(function(name) {
      if (el.classList.contains(name)) wrapper.classList.add('flow-scroll-wrapper-' + name);
    });
    wrapper.setAttribute('data-mode', isAuto ? 'auto' : 'always');
    wrapper.setAttribute('data-visible', 'false');
    el.parentNode.insertBefore(wrapper, el);
    wrapper.appendChild(el);

    var tracks = [];
    if (hasVertical) tracks.push(createTrack(wrapper, el, 'y'));
    if (hasHorizontal) tracks.push(createTrack(wrapper, el, 'x'));
    el._trackUpdaters = tracks;

    function updateAll() {
      tracks.forEach(function(track) {
        track.update();
      });
    }

    function emitPosition() {
      if (typeof window.CustomEvent === 'function') {
        window.dispatchEvent(new CustomEvent('flow-scroll:position', { detail: { element: el } }));
      }
    }

    el.addEventListener('scroll', function() {
      updateAll();
      emitPosition();
      if (!isAuto) return;
      wrapper.setAttribute('data-visible', 'true');
      clearTimeout(el._scrollHideTimer);
      el._scrollHideTimer = setTimeout(function() {
        wrapper.setAttribute('data-visible', 'false');
      }, 800);
    }, { passive: true });

    var ro = new ResizeObserver(function() {
      updateAll();
      emitPosition();
    });
    ro.observe(el);
    updateAll();
    emitPosition();
  }

  function createTrack(wrapper, el, axis) {
    var track = document.createElement('div');
    track.className = 'flow-scrollbar-track';
    track.setAttribute('data-axis', axis);
    var thumb = document.createElement('div');
    thumb.className = 'flow-scrollbar-thumb';
    thumb.setAttribute('data-dragging', 'false');
    track.appendChild(thumb);
    wrapper.appendChild(track);

    var isVertical = axis === 'y';
    function getScrollSize() { return isVertical ? el.scrollHeight : el.scrollWidth; }
    function getClientSize() { return isVertical ? el.clientHeight : el.clientWidth; }
    function getScrollPos() { return isVertical ? el.scrollTop : el.scrollLeft; }
    function getTrackSize() { return isVertical ? track.clientHeight : track.clientWidth; }
    function setScrollPos(value) {
      if (isVertical) el.scrollTop = value;
      else el.scrollLeft = value;
      if (typeof window.CustomEvent === 'function') {
        window.dispatchEvent(new CustomEvent('flow-scroll:position', { detail: { element: el } }));
      }
    }

    function update() {
      var scrollSize = getScrollSize();
      var clientSize = getClientSize();
      if (scrollSize <= clientSize || clientSize <= 0) {
        track.style.display = 'none';
        return;
      }
      track.style.display = '';
      var trackSize = getTrackSize();
      var ratio = clientSize / scrollSize;
      var thumbSize = Math.max(sbTweaks.thumbMin, Math.round(trackSize * ratio));
      var maxScroll = scrollSize - clientSize;
      var maxThumbOffset = Math.max(trackSize - thumbSize, 1);
      var thumbOffset = maxScroll > 0 ? (getScrollPos() / maxScroll) * maxThumbOffset : 0;
      if (isVertical) {
        thumb.style.height = thumbSize + 'px';
        thumb.style.top = thumbOffset + 'px';
        thumb.style.width = sbTweaks.thumbSize + 'px';
      } else {
        thumb.style.width = thumbSize + 'px';
        thumb.style.left = thumbOffset + 'px';
        thumb.style.height = sbTweaks.thumbSize + 'px';
      }
    }

    var isDragging = false;
    var dragStart = 0;
    var dragStartScroll = 0;

    thumb.addEventListener('mousedown', function(event) {
      event.preventDefault();
      event.stopPropagation();
      isDragging = true;
      dragStart = isVertical ? event.clientY : event.clientX;
      dragStartScroll = getScrollPos();
      thumb.setAttribute('data-dragging', 'true');
      document.body.style.userSelect = 'none';
    });

    document.addEventListener('mousemove', function(event) {
      if (!isDragging) return;
      event.preventDefault();
      var current = isVertical ? event.clientY : event.clientX;
      var delta = current - dragStart;
      var trackSize = getTrackSize();
      var scrollSize = getScrollSize();
      var clientSize = getClientSize();
      var thumbSize = Math.max(sbTweaks.thumbMin, Math.round(trackSize * (clientSize / scrollSize)));
      var maxThumbOffset = trackSize - thumbSize;
      var maxScroll = scrollSize - clientSize;
      if (maxThumbOffset > 0 && maxScroll > 0) setScrollPos(dragStartScroll + (delta / maxThumbOffset) * maxScroll);
    });

    document.addEventListener('mouseup', function() {
      if (!isDragging) return;
      isDragging = false;
      thumb.setAttribute('data-dragging', 'false');
      document.body.style.userSelect = '';
    });

    track.addEventListener('mousedown', function(event) {
      if (event.target === thumb) return;
      event.preventDefault();
      var trackRect = track.getBoundingClientRect();
      var clickPos = isVertical ? event.clientY - trackRect.top : event.clientX - trackRect.left;
      var trackSize = getTrackSize();
      var scrollSize = getScrollSize();
      var clientSize = getClientSize();
      var thumbSize = Math.max(sbTweaks.thumbMin, Math.round(trackSize * (clientSize / scrollSize)));
      var maxThumbOffset = trackSize - thumbSize;
      var maxScroll = scrollSize - clientSize;
      var thumbOffset = Math.max(0, Math.min(maxThumbOffset, clickPos - thumbSize / 2));
      if (maxThumbOffset > 0 && maxScroll > 0) setScrollPos((thumbOffset / maxThumbOffset) * maxScroll);
    });

    return { update: update };
  }

  function initWheelScrollNone(root) {
    var scope = root || document;
    findAll(scope, '.flow-scroll-none').forEach(function(el) {
      if (el.dataset.flowScrollNoneBound === 'true') return;
      var style = getComputedStyle(el);
      if (style.overflowX !== 'auto' && style.overflowX !== 'scroll') return;
      el.dataset.flowScrollNoneBound = 'true';
      var targetScroll = el.scrollLeft;
      var currentScroll = targetScroll;
      var isActive = false;

      function tick() {
        var diff = targetScroll - currentScroll;
        if (Math.abs(diff) < 0.5) {
          currentScroll = targetScroll;
          el.scrollLeft = currentScroll;
          isActive = false;
          return;
        }
        currentScroll += diff * 0.15;
        el.scrollLeft = currentScroll;
        requestAnimationFrame(tick);
      }

      el.addEventListener('wheel', function(event) {
        if (Math.abs(event.deltaY) <= Math.abs(event.deltaX)) return;
        event.preventDefault();
        targetScroll = Math.max(0, Math.min(el.scrollWidth - el.clientWidth, targetScroll + event.deltaY));
        if (!isActive) {
          isActive = true;
          currentScroll = el.scrollLeft;
          requestAnimationFrame(tick);
        }
      }, { passive: false });
    });
  }

  function initAll(root) {
    var scope = root || document;
    findAll(scope, '.flow-scroll').forEach(initCustomScrollbar);
    initWheelScrollNone(scope);
    if (typeof window.CustomEvent === 'function') window.dispatchEvent(new CustomEvent('flow-scroll:init', { detail: { root: scope } }));
  }

  function findAll(scope, selector) {
    var items = [];
    if (scope && scope.matches && scope.matches(selector)) items.push(scope);
    if (scope && scope.querySelectorAll) {
      scope.querySelectorAll(selector).forEach(function(el) {
        items.push(el);
      });
    }
    return items;
  }

  window.FlowScroll = {
    initCustomScrollbar: initCustomScrollbar,
    initAll: initAll
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
      initAll(document);
    });
  } else {
    initAll(document);
  }
})();
