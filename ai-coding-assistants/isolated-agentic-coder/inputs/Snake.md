# Snake

A tiny browser Snake game. Should run on modern browsers, playable with arrow keys.

## Requirements

- Arrow keys control the snake. No other input is needed during play.
- The player gets three lives per session. Hitting a wall or the snake's own body costs one life.
- When all three lives are lost, display "Game Over" with the final score and exit.
- Ctrl+R restarts the session (lives back to 3, score back to 0) at any point.

## Acceptance criteria

1. Arrow keys move the snake; pressing the opposite direction is ignored.
2. Eating food increments the displayed score by 1 and grows the snake by one segment.
3. Colliding with a wall or the snake's own body decrements lives by 1.
4. After three deaths, the game over screen shows the final score.
5. Ctrl+R resets lives to 3 and score to 0 without relaunching the binary.
