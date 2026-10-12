import multiprocessing

from secure_novel_gui import SecureNovelApp


multiprocessing.freeze_support()
SecureNovelApp().mainloop()
