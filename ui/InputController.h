#ifndef TROLL_INPUTCONTROLLER_H
#define TROLL_INPUTCONTROLLER_H


#include "FileView.h"
#include "CommandBar.h"

class InputController {

public:
  enum Input {
    CtrlArrowLeft = 0x224,
    CtrlArrowRight = 0x233,
    CtrlArrowDown = 0x210,
    CtrlArrowUp = 0x239,
    ArrowRight = 261,
    ArrowLeft = 260,
    ArrowUp = 259,
    ArrowDown = 258,
    Backspace = 127,
    Enter = 10,
    CtrlX = 24,
  };

  bool handle(const int input, FileView &view, CommandBar &commandBar) {
    if (input == ERR)
      return true;
    if (commandBar.active()) {
      if (input == Enter) {
        commandBar.execute();
      } else if (input == ArrowLeft || input == ArrowRight) {
        commandBar.moveCursor(input == ArrowRight ? 1 : -1);
      } else if (input == Backspace) {
        commandBar.delChar();
      } else if (input == CtrlX) {
        commandBar.deactivate();
      } else if (isprint(input)) {
        commandBar.inputCharacter((char) input);
      } else {
        return false;
      }
    } else {
      if (input == CtrlArrowDown) {
        view.skipDown();
      } else if (input == CtrlArrowUp) {
        view.skipUp();
      } else if (input == CtrlArrowLeft) {
        view.selectWord(-1);
      } else if (input == CtrlArrowRight) {
        view.selectWord(1);
      } else if (input == Backspace) {
        view.delChar();
      } else if (isprint(input)) {
        view.writeChar(input);
      } else if (input == ArrowLeft || input == ArrowRight) {
        view.moveCursorX(input == ArrowRight ? 1 : -1);
      } else if (input == ArrowUp || input == ArrowDown) {
        view.moveCursorY(input == ArrowDown ? 1 : -1);
      } else if (input == CtrlX) {
        commandBar.activate();
      } else {
        return false;
      }
    }
    return true;
  }

};


#endif //TROLL_INPUTCONTROLLER_H
