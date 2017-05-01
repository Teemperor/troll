#ifndef TROLL_FILEVIEW_H
#define TROLL_FILEVIEW_H

#include <curses.h>

#include "File.h"
#include "ColorString.h"
#include "CppHighlighter.h"

class FileView {

  int scrollPos_ = 1;

  int cursorLine = 1;
  int cursor = 0;

  File *file_ = nullptr;

  int x_ = 0;
  int y_ = 0;
  int width_ = 20;
  int height_ = 20;

  ColorString highlight(const std::string &line) {
    return CppHighlighter::highlight(line);
  }

public:
  FileView(int x, int y, int width, int height)
      : x_(x), y_(y), width_(width), height_(height) {
  }

  void resize(int x, int y, int w, int h) {
    x_ = x;
    y_ = y;
    width_ = w;
    height_ = h;
  }

  void setFile(File *file) {
    file_ = file;
    scrollPos_ = 1;
  }

  bool isPrintChar(char c) {
    return !isSpace(c) && isprint(c);
  }

  bool isSpace(char c) {
    return c == ' ' || c == '\t';
  }

  char currentCharOrSpaceEnd() {
    if (cursor == getCurrentLine().content().size())
      return ' ';
    return currentChar();
  }

  char currentChar() {
    if (cursor < getCurrentLine().content().size())
      return getCurrentLine().content().at(cursor);
    return 0;
  }

  void selectWord(int direction) {
    while (isPrintChar(currentCharOrSpaceEnd()))
      if (!moveCursorX(direction))
        break;
    while (isSpace(currentCharOrSpaceEnd()))
      if (!moveCursorX(direction))
        break;
  }

  void writeChar(char c) {
    getCurrentLine().insert(c, cursor);
    moveCursorX(1);
  }

  void delChar() {
    moveCursorX(-1);
    getCurrentLine().erase(cursor);
  }

  void scrollView(long diff) {
    scrollPos_ += diff;
    if (scrollPos_ < 1)
      scrollPos_ = 1;
  }

  bool moveCursorX(int dx) {
    auto oldValue = cursor;
    cursor += dx;
    cursor = std::max(0, cursor);
    cursor = std::min(cursor, (int) getCurrentLine().content().size());
    return oldValue != cursor;
  }

  bool moveCursorY(int dy) {
    cursorLine += dy;
    if (cursorLine < 1) {
      cursorLine = 1;
      return false;
    }
    if (cursorLine > file_->content().linecount()) {
      cursorLine = file_->content().linecount();
      return false;
    }

    cursor = std::min(cursor, (int) getCurrentLine().content().size());

    if (cursorLine > scrollPos_ + height_) {
      scrollPos_ = cursorLine - height_;
    } else if (cursorLine < scrollPos_) {
      scrollPos_ = cursorLine;
    }
    return true;
  }

  Line &getCurrentLine() {
    return file_->content().line(cursorLine);
  }

  void skipDown() {
    moveCursorY(1);
    while (moveCursorY(1)) {
      if (getCurrentLine().onlyWhitespace()) {
        moveCursorY(-1);
        break;
      }
    }
  }

  void skipUp() {
    moveCursorY(-1);
    while (moveCursorY(-1)) {
      if (getCurrentLine().onlyWhitespace()) {
        moveCursorY(1);
        break;
      }
    }
  }

  void display() {
    for (int i = 0; i <= height_; i++) {
      int lineIndex = i + scrollPos_;
      if (lineIndex < file_->content().linecount()) {
        std::string line = file_->content().line(lineIndex).content();
        ColorString str = highlight(line);
        if (lineIndex == cursorLine) {
          str.setCursor(cursor);
        }
        str.display(x_, i, 180);
      } else {
        std::string line;
        line.resize(180, ' ');
        mvprintw(i, y_, "%s", line.c_str());
      }
    }
  }

};


#endif //TROLL_FILEVIEW_H
