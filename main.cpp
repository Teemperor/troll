#include <iostream>

/*

  CURIN1.C
  ========
  (c) Copyright Paul Griffiths 1999
  Email: mail@paulgriffiths.net

  Demonstrating basic ncurses single key input.

*/


#include <stdlib.h>
#include <curses.h>
#include <FileView.h>
#include <InputController.h>
#include <signal.h>
#include <string.h>
#include <TabManager.h>


char *intprtkey(int ch);

WINDOW *mainwin;

void init_ncurses() {

  /*  Initialize ncurses  */

  if ((mainwin = initscr()) == NULL) {
    fprintf(stderr, "Error initializing ncurses.\n");
    exit(EXIT_FAILURE);
  }

  noecho();                  /*  Turn off key echoing                 */
  keypad(mainwin, TRUE);     /*  Enable the keypad for non-char keys  */



  assert(has_colors());
  start_color();      /* Start color 			*/
  use_default_colors();
  init_pair(1, COLOR_WHITE, -1);
  init_pair(2, COLOR_YELLOW, -1);
  init_pair(3, COLOR_BLUE, -1);
  init_pair(4, COLOR_RED, -1);
  init_pair(5, COLOR_CYAN, -1);
  init_pair(6, COLOR_GREEN, -1);
  init_pair(7, COLOR_MAGENTA, -1);

  /*  Print a prompt and refresh() the screen  */

  refresh();
}

void deinit_ncurses() {

  delwin(mainwin);
  endwin();
  refresh();
}


bool resized_ = false;

void handle_winch(int sig) {
  resized_ = true;
}

int main(int argc, char ** argv) {
  struct sigaction sa;
  memset(&sa, 0, sizeof(struct sigaction));
  sa.sa_handler = handle_winch;
  sigaction(SIGWINCH, &sa, NULL);

  setlocale(LC_CTYPE, "");

  File file("test");

  init_ncurses();

  InputController controller;

  CommandBar commandBar(0, LINES - 1, COLS);

  TabManager manager(0, 0, COLS, LINES - 2);
  manager.openFile("test");


  timeout(300);

  int ch = 0;
  bool handledInput = true;
  while (!commandBar.shouldExit()) {
    if (resized_) {
      resized_ = false;
      endwin();
      refresh();
      commandBar.resize(0, LINES - 1, COLS);
      manager.resize(0, 0, COLS, LINES - 2);
    }

    manager.display();
    commandBar.display();
    //deleteln();
    if (!handledInput) {
      attron(COLOR_PAIR(1));
      mvprintw(0, 50, "You pressed: %d (%s)", ch, intprtkey(ch));
      attroff(COLOR_PAIR(1));
    }

    refresh();

    ch = getch();
    handledInput = controller.handle(ch, *manager.getView(), commandBar);
  }


  /*  Clean up after ourselves  */
  deinit_ncurses();


  return EXIT_SUCCESS;
}


/*  Struct to hold keycode/keyname information  */

struct keydesc {
  int code;
  char name[20];
};


/*  Returns a string describing a character passed to it  */

char *intprtkey(int ch) {

  /*  Define a selection of keys we will handle  */

  static struct keydesc keys[] = {{KEY_UP,        "Up arrow"},
                                  {KEY_DOWN,      "Down arrow"},
                                  {KEY_LEFT,      "Left arrow"},
                                  {KEY_RIGHT,     "Right arrow"},
                                  {KEY_HOME,      "Home"},
                                  {KEY_END,       "End"},
                                  {KEY_BACKSPACE, "Backspace"},
                                  {KEY_IC,        "Insert"},
                                  {KEY_DC,        "Delete"},
                                  {KEY_NPAGE,     "Page down"},
                                  {KEY_PPAGE,     "Page up"},
                                  {KEY_F(1),      "Function key 1"},
                                  {KEY_F(2),      "Function key 2"},
                                  {KEY_F(3),      "Function key 3"},
                                  {KEY_F(4),      "Function key 4"},
                                  {KEY_F(5),      "Function key 5"},
                                  {KEY_F(6),      "Function key 6"},
                                  {KEY_F(7),      "Function key 7"},
                                  {KEY_F(8),      "Function key 8"},
                                  {KEY_F(9),      "Function key 9"},
                                  {KEY_F(10),     "Function key 10"},
                                  {KEY_F(11),     "Function key 11"},
                                  {KEY_F(12),     "Function key 12"},
                                  {-1,            "<unsupported>"}
  };
  static char keych[2] = {0};

  if (isprint(ch) && !(ch & KEY_CODE_YES)) {

    /*  If a printable character  */

    keych[0] = ch;
    return keych;
  } else {

    /*  Non-printable, so loop through our array of structs  */

    int n = 0;

    do {
      if (keys[n].code == ch)
        return keys[n].name;
      n++;
    } while (keys[n].code != -1);

    return keys[n].name;
  }

  return NULL;        /*  We shouldn't get here  */
}