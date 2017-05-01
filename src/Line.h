#ifndef TROLL_LINE_H
#define TROLL_LINE_H

#include <string>

class Line {

  std::string content_;

public:
  Line() {
  }

  Line(const std::string &content) : content_(content) {
  }

  void insert(char c, int pos) {
    content_.insert(content().begin() + pos, c);
  }

  bool onlyWhitespace() {
    for (char c : content_) {
      if (!(c == ' ' || c == '\t' || c == '\n'))
        return false;
    }
    return true;
  }

  void erase(int pos) {
    content_.erase(pos, 1);
  }

  const std::string &content() const {
    return content_;
  }
};


#endif //TROLL_LINE_H
