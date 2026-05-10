'''
Created on Dec 26, 2025

@author: matze
'''

from PyQt6 import QtCore, QtGui
import signal

#takes the requests and only sends the actual framenumber to the player - makes the slider faster and the queue lighter        
class SliderThread(QtCore.QThread):
    def __init__(self,func):
        QtCore.QThread.__init__(self)
        self.delay=0
        self.condition = QtCore.QWaitCondition()
        self.mutex = QtCore.QMutex()
        self.func=func #function will be executed here, not in main thread
        self.pos=0
        #self.current=0
        self.__running=True
        self.start()
        
    def run(self):
        while self.__running:
            curr=-1
            while self.pos != curr:
                curr=self.pos
                self.func(self.pos)

            self.__wait() #wait until needed
    
    def __wait(self):
        self.mutex.lock()
        self.condition.wait(self.mutex)
        self.mutex.unlock()
    
    def stop(self):
        self.__running=False
        self.condition.wakeOne()
    
    def seekTo(self,fn):
        self.pos = fn
        self.condition.wakeOne()#wake up the long wait
        
def installSigIntHandler(app):
    """Allow Ctrl+C to quit a PyQt app cleanly.
    Qt's event loop blocks Python signal handling, so a short timer
    is used to periodically yield back to the Python interpreter."""
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    timer = QtCore.QTimer()
    timer.start(200)
    timer.timeout.connect(lambda: None)
    return timer  # caller must keep a reference to prevent GC

def is_theme_dark(widget):
    """Returns True if the widget's background is dark, False if light."""
    color = widget.palette().color(QtGui.QPalette.ColorRole.Window)
    luminance = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255
    return luminance < 0.5