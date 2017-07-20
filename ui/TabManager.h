#ifndef TROLL_TABMANAGER_H
#define TROLL_TABMANAGER_H


#include <list>
#include "FileView.h"

class TabManager {

  std::size_t selectedIndex = 0;
  FileView *currentView_ = nullptr;
  std::list<FileView *> views_;

  int x_ = 0;
  int y_ = 0;
  int width_ = 20;
  int height_ = 20;

public:
  TabManager(int x, int y, int width, int height)
      : x_(x), y_(y), width_(width), height_(height) {
  }

  bool openFile(const std::string& path, bool handleLineNumber = true) {
    FileView* view;
    views_.push_back(view = new FileView(x_, y_, width_, height_));
    view->setFile(new File(path));
    currentView_ = view;
    return true;
  }


  void resize(int x, int y, int w, int h) {
    for (FileView * f : views_) {
      f->resize(x, y, w, h);
    }
  }

  FileView *getView() {
    return currentView_;
  }

  void display() {
    if (currentView_)
      currentView_->display();
  }

};


#endif //TROLL_TABMANAGER_H
