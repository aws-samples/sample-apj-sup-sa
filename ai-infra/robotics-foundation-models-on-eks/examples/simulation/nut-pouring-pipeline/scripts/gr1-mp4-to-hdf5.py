import glob
import os
import sys
from pathlib import Path

import cv2
import h5py
import numpy as np


def frames_from_mp4(video_path, target_height, target_width):
    video = cv2.VideoCapture(str(video_path))
    frames = []
    try:
        while True:
            ok, frame = video.read()
            if not ok:
                break
            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            frame = cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_LINEAR)
            frames.append(frame)
    finally:
        video.release()
    if not frames:
        raise RuntimeError(f"no frames decoded from {video_path}")
    return np.asarray(frames, dtype=np.uint8)


def main():
    input_file = Path(sys.argv[1])
    videos_dir = Path(sys.argv[2])
    output_file = Path(sys.argv[3])
    video_paths = sorted(glob.glob(str(videos_dir / "*.mp4")))
    print(f"Found {len(video_paths)} MP4 videos in {videos_dir}")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with h5py.File(input_file, "r") as f_in, h5py.File(output_file, "w") as f_out:
        f_in.copy("data", f_out)
        demo_ids = [int(key.split("_")[1]) for key in f_in["data"].keys()]
        next_demo_id = max(demo_ids) + 1
        print(f"Starting new demos from ID: {next_demo_id}")

        for video_path in video_paths:
            video_name = os.path.basename(video_path)
            orig_demo_id = int(video_name.split("_")[1])
            source_demo = f"data/demo_{orig_demo_id}"
            new_demo = f"demo_{next_demo_id}"
            f_in.copy(source_demo, f_out["data"], name=new_demo)

            obs = f_out[f"data/{new_demo}/obs"]
            camera_key = "robot_pov_cam" if "robot_pov_cam" in obs else "table_cam"
            original_shape = f_in[f"{source_demo}/obs/{camera_key}"].shape
            target_height, target_width = original_shape[1:3]
            frames = frames_from_mp4(video_path, target_height, target_width)
            if frames.shape[0] != original_shape[0]:
                print(
                    f"Warning: {video_name} frame count {frames.shape[0]} differs from "
                    f"source demo frame count {original_shape[0]}"
                )

            del obs[camera_key]
            obs.create_dataset(camera_key, data=frames, compression="gzip")
            print(f"Added augmented {new_demo} from demo_{orig_demo_id} using {camera_key}")
            next_demo_id += 1

    print(f"Augmented data saved to {output_file}")


if __name__ == "__main__":
    main()
