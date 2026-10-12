import multiprocessing

from secure_novel_gui import SecureNovelApp


multiprocessing.freeze_support()

if __name__ == "__main__":
    SecureNovelApp().mainloop()
