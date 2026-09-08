// Wait till the browser is ready to render the game (avoids glitches)
window.requestAnimationFrame(function () {
  window.__AVATAR_GAME__ = new GameManager(4, KeyboardInputManager, HTMLActuator, LocalStorageManager);
});
