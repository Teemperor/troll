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
        Backspace = 127,
        Enter = 10,
        CtrlX = 24,
    };

    void handle(const int input, FileView& view, CommandBar& commandBar) {
        if (input == CtrlArrowDown) {
            view.scrollView(1);
        }
        if (input == CtrlArrowUp) {
            view.scrollView(-1);
        }
        if (input == CtrlX) {
            commandBar.activate();
        }
        if (commandBar.active()) {
            // check for enter
            if (input == Enter) {
                commandBar.execute();
            } else if (input == ArrowLeft || input == ArrowRight) {
                commandBar.moveCursor(input == ArrowRight ? 1 : -1);
            } else if (input == Backspace) {
                commandBar.delChar();
            }
            else if (isprint(input)) {
                commandBar.inputCharacter((char) input);
            }
        }
    }

};


#endif //TROLL_INPUTCONTROLLER_H
