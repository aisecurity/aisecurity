import sys
import argparse

sys.path.insert(1, "../")

from facenet import FaceNet
from util.common import ON_CUDA, ON_JETSON

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", type=str, help="an integer for the accumulator")
    valid_face_rec_modes = [
        "cosine",
        "adaptive",
    ]

    args = parser.parse_args()

    if args.mode is None:
        args.mode = "cosine"
    elif args.mode is not None and args.mode not in valid_face_rec_modes:
        raise Exception("User inputted an invalid face-rec-mode")

    detector = "mtcnn"  # "trt-mtcnn" if ON_CUDA else "mtcnn"
    graphics = True  # not ON_JETSON
    mtcnn_stride = 7 if ON_JETSON else 3
    resize = 1 if ON_JETSON else 0.6

    facenet = FaceNet()
    facenet.real_time_recognize(
        detector=detector,
        graphics=graphics,
        mtcnn_stride=mtcnn_stride,
        resize=resize,
        mode=args.mode,
    )
