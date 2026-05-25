# Copyright 2025-2026 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Spatial Memory module for creating a semantic map of the environment.
"""

from datetime import datetime
import os
import shutil
from threading import RLock
import time
from typing import TYPE_CHECKING, Any
import uuid

import cv2
import numpy as np
from reactivex import Observable, interval, operators as ops
from reactivex.disposable import Disposable

from dimos.agents_deprecated.memory.image_embedding import ImageEmbeddingProvider
from dimos.agents_deprecated.memory.spatial_vector_db import SpatialVectorDB
from dimos.agents_deprecated.memory.visual_memory import VisualMemory
from dimos.constants import DIMOS_PROJECT_ROOT
from dimos.core.coordination.module_coordinator import ModuleCoordinator
from dimos.core.core import rpc
from dimos.core.module import Module, ModuleConfig
from dimos.core.stream import In
from dimos.msgs.sensor_msgs.Image import Image
from dimos.spec.perception import Camera
from dimos.types.robot_location import RobotLocation
from dimos.utils.logging_config import setup_logger

if TYPE_CHECKING:
    from dimos.msgs.geometry_msgs.Vector3 import Vector3

_OUTPUT_DIR = DIMOS_PROJECT_ROOT / "assets" / "output"
_MEMORY_DIR = _OUTPUT_DIR / "memory"
_SPATIAL_MEMORY_DIR = _MEMORY_DIR / "spatial_memory"
_DB_PATH = _SPATIAL_MEMORY_DIR / "chromadb_data"
_VISUAL_MEMORY_PATH = _SPATIAL_MEMORY_DIR / "visual_memory.pkl"

logger = setup_logger()


_MAX_ROBOT_LOCATIONS = 1000


def _wipe_chromadb_directory(db_path: str) -> None:
    """Remove all Chroma/SQLite files under *db_path*."""
    if not os.path.isdir(db_path):
        return
    for item in os.listdir(db_path):
        item_path = os.path.join(db_path, item)
        if os.path.isfile(item_path) or os.path.islink(item_path):
            os.unlink(item_path)
        elif os.path.isdir(item_path):
            shutil.rmtree(item_path)


def _create_persistent_chroma_client(db_path: str) -> Any:
    """Open Chroma persistent store; recreate on corruption."""
    import chromadb
    from chromadb.config import Settings

    os.makedirs(db_path, exist_ok=True)
    settings = Settings(anonymized_telemetry=False)

    def _open() -> Any:
        client = chromadb.PersistentClient(path=db_path, settings=settings)
        client.heartbeat()  # type: ignore[attr-defined]
        return client

    try:
        return _open()
    except Exception as exc:
        logger.warning("ChromaDB open failed (%s); wiping and recreating at %s", exc, db_path)
        _wipe_chromadb_directory(db_path)
        os.makedirs(db_path, exist_ok=True)
        return _open()


class SpatialConfig(ModuleConfig):
    collection_name: str = "spatial_memory"
    embedding_model: str = "clip"
    embedding_dimensions: int = 512
    min_distance_threshold: float = 0.01  # Min distance in meters to store a new frame
    min_time_threshold: float = 1.0  # Min time in seconds to record a new frame
    max_stored_frames: int = 500  # FIFO cap on stored frames (0 = unlimited)
    max_room_images: int = 100  # Max room reference images; oldest evicted when exceeded
    db_path: str | None = str(_DB_PATH)  # Path for ChromaDB persistence
    visual_memory_path: str | None = str(
        _VISUAL_MEMORY_PATH
    )  # Path for saving/loading visual memory
    new_memory: bool = False  # Whether to create a new memory from scratch
    output_dir: str | None = str(_SPATIAL_MEMORY_DIR)  # Directory for storing visual memory data
    chroma_client: Any = None  # Optional ChromaDB client for persistence
    visual_memory: VisualMemory | None = None  # Optional VisualMemory instance for storing images


class SpatialMemory(Module):
    """
    A Dimos module for building and querying Robot spatial memory.

    This module processes video frames and odometry data from LCM streams,
    associates them with XY locations, and stores them in a vector database
    for later retrieval via RPC calls. It also maintains a list of named
    robot locations that can be queried by name.
    """

    config: SpatialConfig

    # LCM inputs
    color_image: In[Image]

    def __init__(self, **kwargs: Any) -> None:
        """
        Initialize the spatial perception system.

        Args:
            collection_name: Name of the vector database collection
            embedding_model: Model to use for image embeddings ("clip", "resnet", etc.)
            embedding_dimensions: Dimensions of the embedding vectors
            min_distance_threshold: Minimum distance in meters to record a new frame
            min_time_threshold: Minimum time in seconds to record a new frame
            chroma_client: Optional ChromaDB client for persistent storage
            visual_memory: Optional VisualMemory instance for storing images
            output_dir: Directory for storing visual memory data if visual_memory is not provided
        """
        super().__init__(**kwargs)

        self.collection_name = self.config.collection_name
        self.embedding_model = self.config.embedding_model
        self.embedding_dimensions = self.config.embedding_dimensions
        self.min_distance_threshold = self.config.min_distance_threshold
        self.min_time_threshold = self.config.min_time_threshold
        self.db_path = self.config.db_path
        self.visual_memory_path = self.config.visual_memory_path

        self._chroma_client = self.config.chroma_client
        if self.config.new_memory and self.db_path:
            logger.info("Creating new ChromaDB database (new_memory=True)")
            _wipe_chromadb_directory(self.db_path)

        # Initialize or load visual memory
        self._visual_memory = self.config.visual_memory
        if self._visual_memory is None:
            if self.config.new_memory or not os.path.exists(self.visual_memory_path or ""):
                logger.info("Creating new visual memory")
                self._visual_memory = VisualMemory(output_dir=self.config.output_dir)
            else:
                try:
                    logger.info(f"Loading existing visual memory from {self.visual_memory_path}...")
                    self._visual_memory = VisualMemory.load(
                        self.visual_memory_path,  # type: ignore[arg-type]
                        output_dir=self.config.output_dir,
                    )
                    logger.info(f"Loaded {self._visual_memory.count()} images from previous runs")
                except Exception as e:
                    logger.error(f"Error loading visual memory: {e}")
                    self._visual_memory = VisualMemory(output_dir=self.config.output_dir)

        self.embedding_provider = ImageEmbeddingProvider(
            model_name=self.embedding_model, dimensions=self.embedding_dimensions
        )

        self._chroma_lock = RLock()
        self._chroma_recover_cooldown_until: float = 0.0
        self._setup_chromadb()

        self.max_stored_frames: int = self.config.max_stored_frames
        self.max_room_images: int = self.config.max_room_images
        self._room_image_ids: list[str] = []  # FIFO order for room image eviction
        self.max_stored_frames = self.config.max_stored_frames

        self.vector_db: SpatialVectorDB = SpatialVectorDB(
            collection_name=self.collection_name,
            chroma_client=self._chroma_client,
            visual_memory=self._visual_memory,
            embedding_provider=self.embedding_provider,
            max_stored_frames=self.max_stored_frames,
        )

        self.last_position: Vector3 | None = None
        self.last_record_time: float | None = None

        self.frame_count: int = 0
        self.stored_frame_count: int = 0
        self._stored_frame_ids: list[str] = []  # FIFO order for eviction

        # List to store robot locations
        self.robot_locations: list[RobotLocation] = []

        # Track latest data for processing
        self._latest_video_frame: np.ndarray | None = None
        self._process_interval = 1

        logger.info(f"SpatialMemory initialized with model {self.embedding_model}")

    def _setup_chromadb(self, *, wipe: bool = False) -> None:
        """Create or recreate Chroma client, vector DB, and room collection."""
        if wipe and self.db_path:
            _wipe_chromadb_directory(self.db_path)

        if self._chroma_client is None and self.db_path is not None:
            self._chroma_client = _create_persistent_chroma_client(self.db_path)

        self.vector_db = SpatialVectorDB(
            collection_name=self.collection_name,
            chroma_client=self._chroma_client,
            visual_memory=self._visual_memory,
            embedding_provider=self.embedding_provider,
        )

        room_collection_name = f"{self.collection_name}_room_images"
        if self._chroma_client is not None:
            self.room_collection = self._chroma_client.get_or_create_collection(
                name=room_collection_name, metadata={"hnsw:space": "cosine"}
            )
        else:
            import chromadb

            self.room_collection = chromadb.Client().get_or_create_collection(
                name=room_collection_name, metadata={"hnsw:space": "cosine"}
            )

    def _chroma_error_recoverable(self, exc: BaseException) -> bool:
        msg = str(exc).lower()
        return any(
            token in msg
            for token in (
                "readonly database",
                "no such table",
                "failed to get segments",
                "database error",
            )
        )

    def _recover_chromadb_if_needed(self, exc: BaseException) -> bool:
        if not self._chroma_error_recoverable(exc) or self.db_path is None:
            return False
        now = time.time()
        if now < self._chroma_recover_cooldown_until:
            return False
        self._chroma_recover_cooldown_until = now + 30.0
        logger.warning(
            "ChromaDB appears corrupt; wiping %s and re-binding collections", self.db_path
        )
        try:
            with self._chroma_lock:
                self._chroma_client = None
                self._setup_chromadb(wipe=True)
            return True
        except Exception:
            logger.exception("ChromaDB recovery failed")
            return False

    @rpc
    def start(self) -> None:
        super().start()

        # Subscribe to LCM streams
        def set_video(image_msg: Image) -> None:
            # Convert Image message to numpy array
            if hasattr(image_msg, "data"):
                frame = image_msg.data
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                self._latest_video_frame = frame
            else:
                logger.warning("Received image message without data attribute")

        self.register_disposable(Disposable(self.color_image.subscribe(set_video)))

        # Start periodic processing using interval
        self.register_disposable(
            interval(self._process_interval).subscribe(lambda _: self._process_frame())
        )

    @rpc
    def stop(self) -> None:
        # Save data before shutdown
        self.save()

        if self._visual_memory:
            self._visual_memory.clear()

        super().stop()

    def _process_frame(self) -> None:
        """Process the latest frame with pose data if available."""
        tf = self.tf.get("world", "base_link")

        if tf is None:
            return

        if self._latest_video_frame is None:
            return

        # Create Pose object with position and orientation
        current_pose = tf.to_pose()

        # Process the frame directly
        try:
            self.frame_count += 1

            # Check distance constraint
            if self.last_position is not None:
                distance_moved = np.linalg.norm(
                    [
                        current_pose.position.x - self.last_position.x,
                        current_pose.position.y - self.last_position.y,
                        current_pose.position.z - self.last_position.z,
                    ]
                )
                if distance_moved < self.min_distance_threshold:
                    logger.debug(
                        f"Position has not moved enough: {distance_moved:.4f}m < {self.min_distance_threshold}m, skipping frame"
                    )
                    return

            # Check time constraint
            if self.last_record_time is not None:
                time_elapsed = time.time() - self.last_record_time
                if time_elapsed < self.min_time_threshold:
                    logger.debug(
                        f"Time since last record too short: {time_elapsed:.2f}s < {self.min_time_threshold}s, skipping frame"
                    )
                    return

            current_time = time.time()

            # Get embedding for the frame
            frame_embedding = self.embedding_provider.get_embedding(self._latest_video_frame)

            frame_id = f"frame_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
            # Get euler angles from quaternion orientation for metadata
            euler = tf.rotation.to_euler()

            # Create metadata dictionary with primitive types only
            metadata = {
                "pos_x": float(current_pose.position.x),
                "pos_y": float(current_pose.position.y),
                "pos_z": float(current_pose.position.z),
                "rot_x": float(euler.x),
                "rot_y": float(euler.y),
                "rot_z": float(euler.z),
                "timestamp": current_time,
                "frame_id": frame_id,
            }

            # Store in vector database. Chroma recovery may recreate collections,
            # so serialize DB access with recovery and room-image queries.
            with self._chroma_lock:
                self.vector_db.add_image_vector(
                    vector_id=frame_id,
                    image=self._latest_video_frame,
                    embedding=frame_embedding,
                    metadata=metadata,
                )

                # Update tracking variables only after the DB write succeeds.
                self.last_position = current_pose.position
                self.last_record_time = current_time
                self.stored_frame_count += 1
                self._stored_frame_ids.append(frame_id)

                # Evict oldest frames when exceeding the max cap
                while len(self._stored_frame_ids) > self.max_stored_frames:
                    evict_id = self._stored_frame_ids.pop(0)
                    try:
                        self.vector_db.image_collection.delete(ids=[evict_id])
                    except Exception:
                        pass
                    if self._visual_memory is not None:
                        self._visual_memory.images.pop(evict_id, None)

            logger.info(
                f"Stored frame at position ({current_pose.position.x:.2f}, {current_pose.position.y:.2f}, {current_pose.position.z:.2f}), "
                f"rotation ({euler.x:.2f}, {euler.y:.2f}, {euler.z:.2f}) "
                f"stored {self.stored_frame_count}/{self.frame_count} frames "
                f"(mem={len(self._stored_frame_ids)}/{self.max_stored_frames})"
            )

            # Periodically save visual memory to disk
            if self._visual_memory is not None and self.visual_memory_path is not None:
                if self.stored_frame_count % 100 == 0:
                    self.save()

        except Exception as e:
            if self._recover_chromadb_if_needed(e):
                logger.warning("ChromaDB recovered after frame error; skipping this frame")
            else:
                logger.error(f"Error processing frame: {e}")

    @rpc
    def query_by_location(
        self, x: float, y: float, radius: float = 2.0, limit: int = 5
    ) -> list[dict]:  # type: ignore[type-arg]
        """
        Query the vector database for images near the specified location.

        Args:
            x: X coordinate
            y: Y coordinate
            radius: Search radius in meters
            limit: Maximum number of results to return

        Returns:
            List of results, each containing the image and its metadata
        """
        return self.vector_db.query_by_location(x, y, radius, limit)

    @rpc
    def save(self) -> bool:
        """
        Save the visual memory component to disk.

        Returns:
            True if memory was saved successfully, False otherwise
        """
        if self._visual_memory is not None and self.visual_memory_path is not None:
            try:
                saved_path = self._visual_memory.save(self.visual_memory_path)
                logger.info(f"Saved {self._visual_memory.count()} images to {saved_path}")
                return True
            except Exception as e:
                logger.error(f"Failed to save visual memory: {e}")
        return False

    def process_stream(self, combined_stream: Observable) -> Observable:  # type: ignore[type-arg]
        """
        Process a combined stream of video frames and positions.

        This method handles a stream where each item already contains both the frame and position,
        such as the stream created by combining video and transform streams with the
        with_latest_from operator.

        Args:
            combined_stream: Observable stream of dictionaries containing 'frame' and 'position'

        Returns:
            Observable of processing results, including the stored frame and its metadata
        """

        def process_combined_data(data):  # type: ignore[no-untyped-def]
            self.frame_count += 1

            frame = data.get("frame")
            position_vec = data.get("position")  # Use .get() for consistency
            rotation_vec = data.get("rotation")  # Get rotation data if available

            if position_vec is None or rotation_vec is None:
                logger.info("No position or rotation data available, skipping frame")
                return None

            # position_vec is already a Vector3, no need to recreate it
            position_v3 = position_vec

            if self.last_position is not None:
                distance_moved = np.linalg.norm(
                    [
                        position_v3.x - self.last_position.x,
                        position_v3.y - self.last_position.y,
                        position_v3.z - self.last_position.z,
                    ]
                )
                if distance_moved < self.min_distance_threshold:
                    logger.debug("Position has not moved, skipping frame")
                    return None

            if (
                self.last_record_time is not None
                and (time.time() - self.last_record_time) < self.min_time_threshold
            ):
                logger.debug("Time since last record too short, skipping frame")
                return None

            current_time = time.time()

            frame_embedding = self.embedding_provider.get_embedding(frame)

            frame_id = f"frame_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

            # Create metadata dictionary with primitive types only
            metadata = {
                "pos_x": float(position_v3.x),
                "pos_y": float(position_v3.y),
                "pos_z": float(position_v3.z),
                "rot_x": float(rotation_vec.x),
                "rot_y": float(rotation_vec.y),
                "rot_z": float(rotation_vec.z),
                "timestamp": current_time,
                "frame_id": frame_id,
            }

            self.vector_db.add_image_vector(
                vector_id=frame_id, image=frame, embedding=frame_embedding, metadata=metadata
            )

            self.last_position = position_v3
            self.last_record_time = current_time
            self.stored_frame_count += 1
            self._stored_frame_ids.append(frame_id)

            # Evict oldest frames when exceeding the max cap
            while len(self._stored_frame_ids) > self.max_stored_frames:
                evict_id = self._stored_frame_ids.pop(0)
                try:
                    self.vector_db.image_collection.delete(ids=[evict_id])
                except Exception:
                    pass
                if self._visual_memory is not None:
                    self._visual_memory.images.pop(evict_id, None)

            logger.info(
                f"Stored frame at position ({position_v3.x:.2f}, {position_v3.y:.2f}, {position_v3.z:.2f}), "
                f"rotation ({rotation_vec.x:.2f}, {rotation_vec.y:.2f}, {rotation_vec.z:.2f}) "
                f"stored {self.stored_frame_count}/{self.frame_count} frames "
                f"(mem={len(self._stored_frame_ids)}/{self.max_stored_frames})"
            )

            # Periodically save visual memory to disk
            if self._visual_memory is not None and self.visual_memory_path is not None:
                if self.stored_frame_count % 100 == 0:
                    self.save()

            # Create return dictionary with primitive-compatible values
            return {
                "frame": frame,
                "position": (position_v3.x, position_v3.y, position_v3.z),
                "rotation": (rotation_vec.x, rotation_vec.y, rotation_vec.z),
                "frame_id": frame_id,
                "timestamp": current_time,
            }

        return combined_stream.pipe(
            ops.map(process_combined_data), ops.filter(lambda result: result is not None)
        )

    @rpc
    def query_by_image(self, image: np.ndarray, limit: int = 5) -> list[dict]:  # type: ignore[type-arg]
        """
        Query the vector database for images similar to the provided image.

        Args:
            image: Query image
            limit: Maximum number of results to return

        Returns:
            List of results, each containing the image and its metadata
        """
        embedding = self.embedding_provider.get_embedding(image)
        return self.vector_db.query_by_embedding(embedding, limit)

    @rpc
    def query_by_text(self, text: str, limit: int = 5) -> list[dict]:  # type: ignore[type-arg]
        """
        Query the vector database for images matching the provided text description.

        This method uses CLIP's text-to-image matching capability to find images
        that semantically match the text query (e.g., "where is the kitchen").

        Args:
            text: Text query to search for
            limit: Maximum number of results to return

        Returns:
            List of results, each containing the image, its metadata, and similarity score
        """
        logger.info(f"Querying spatial memory with text: '{text}'")
        return self.vector_db.query_by_text(text, limit)

    @rpc
    def add_robot_location(self, location: RobotLocation) -> bool:
        """
        Add a named robot location to spatial memory.

        Args:
            location: The RobotLocation object to add

        Returns:
            True if successfully added, False otherwise
        """
        try:
            # Dedup by name: replace existing entry with the same name
            existing_idx = None
            for i, loc in enumerate(self.robot_locations):
                if loc.name.lower() == location.name.lower():
                    existing_idx = i
                    break
            if existing_idx is not None:
                self.robot_locations[existing_idx] = location
            else:
                self.robot_locations.append(location)
                # Cap the list; drop the oldest entry when exceeded
                while len(self.robot_locations) > _MAX_ROBOT_LOCATIONS:
                    self.robot_locations.pop(0)

            logger.info(f"Added robot location '{location.name}' at position {location.position}")
            return True

        except Exception as e:
            logger.error(f"Error adding robot location: {e}")
            return False

    @rpc
    def add_named_location(
        self,
        name: str,
        position: list[float] | None = None,
        rotation: list[float] | None = None,
        description: str | None = None,
    ) -> bool:
        """
        Add a named robot location to spatial memory using current or specified position.

        Args:
            name: Name of the location
            position: Optional position [x, y, z], uses current position if None
            rotation: Optional rotation [roll, pitch, yaw], uses current rotation if None
            description: Optional description of the location

        Returns:
            True if successfully added, False otherwise
        """
        tf = self.tf.get("world", "base_link")
        if not tf:
            logger.error("No position available for robot location")
            return False

        # Create RobotLocation object
        location = RobotLocation(  # type: ignore[call-arg]
            name=name,
            position=tf.translation,
            rotation=tf.rotation.to_euler(),
            description=description or f"Location: {name}",
            timestamp=time.time(),
        )

        return self.add_robot_location(location)

    @rpc
    def get_robot_locations(self) -> list[RobotLocation]:
        """
        Get all stored robot locations.

        Returns:
            List of RobotLocation objects
        """
        return self.robot_locations

    @rpc
    def find_robot_location(self, name: str) -> RobotLocation | None:
        """
        Find a robot location by name.

        Args:
            name: Name of the location to find

        Returns:
            RobotLocation object if found, None otherwise
        """
        # Simple search through our list of locations
        for location in self.robot_locations:
            if location.name.lower() == name.lower():
                return location

        return None

    @rpc
    def get_stats(self) -> dict[str, int]:
        """Get statistics about the spatial memory module.

        Returns:
            Dictionary containing:
                - frame_count: Total number of frames processed
                - stored_frame_count: Number of frames actually stored
        """
        return {
            "frame_count": self.frame_count,
            "stored_frame_count": self.stored_frame_count,
            "retained_frame_count": len(self.vector_db._frame_ids),
            "max_stored_frames": self.max_stored_frames,
        }

    @rpc
    def clear_all(self) -> dict[str, int]:
        """Clear ChromaDB collections, visual memory, and tagged locations."""
        room_n = 0
        frame_n = 0
        try:
            room_ids = self.room_collection.get(include=[])["ids"] or []
            room_n = len(room_ids)
        except Exception:
            logger.debug("room_collection unreadable during clear_all", exc_info=True)

        try:
            img_ids = self.vector_db.image_collection.get(include=[])["ids"] or []
            frame_n = len(img_ids)
        except Exception:
            logger.debug("image_collection unreadable during clear_all", exc_info=True)

        loc_n = len(self.robot_locations)
        self.robot_locations.clear()
        self._room_image_ids.clear()
        self._stored_frame_ids.clear()
        self.frame_count = 0
        self.stored_frame_count = 0

        if self._visual_memory:
            self._visual_memory.clear()

        if self.visual_memory_path and os.path.isfile(self.visual_memory_path):
            try:
                os.unlink(self.visual_memory_path)
            except OSError:
                pass

        # Wipe on-disk DB and re-open client (old client kept stale handles after delete).
        self._chroma_client = None
        self._chroma_recover_cooldown_until = 0.0
        if self.db_path:
            self._setup_chromadb(wipe=True)
            logger.info("Recreated ChromaDB at %s", self.db_path)

        logger.info(
            "Spatial memory cleared: room_images=%d frames=%d tagged_locations=%d",
            room_n,
            frame_n,
            loc_n,
        )
        return {"room_images": room_n, "frames": frame_n, "tagged_locations": loc_n}

    @rpc
    def tag_location(self, robot_location: RobotLocation) -> bool:
        try:
            self.vector_db.tag_location(robot_location)
        except Exception:
            return False
        return True

    @rpc
    def query_tagged_location(self, query: str) -> RobotLocation | None:
        location, semantic_distance = self.vector_db.query_tagged_location(query)
        if semantic_distance < 0.3:
            return location
        return None

    @rpc
    def tag_location_with_image(self, robot_location: RobotLocation, image: np.ndarray) -> bool:
        """Tag a location with both a text label and a reference image.

        Stores the camera frame's CLIP embedding in the room_images collection
        so the room can later be recognized visually, even after coordinate shifts.

        Multiple images can be stored for the same room — record from different
        angles / positions to improve recognition robustness.

        Args:
            robot_location: Location with at minimum name and position/rotation.
            image: Camera frame (numpy array, BGR) captured at this location.

        Returns:
            True on success.
        """
        if self._visual_memory is None:
            logger.error("tag_location_with_image: visual memory not initialized")
            return False

        for attempt in range(2):
            try:
                with self._chroma_lock:
                    self.vector_db.tag_location(robot_location)

                    embedding = self.embedding_provider.get_embedding(image)
                    metadata = robot_location.to_vector_metadata()

                    self._visual_memory.add(robot_location.location_id, image)

                    self.room_collection.add(
                        ids=[robot_location.location_id],
                        embeddings=[embedding.tolist()],
                        metadatas=[metadata],
                    )
                    self._room_image_ids.append(robot_location.location_id)

                    while len(self._room_image_ids) > self.max_room_images:
                        evict_id = self._room_image_ids.pop(0)
                        try:
                            self.room_collection.delete(ids=[evict_id])
                        except Exception:
                            pass
                        self._visual_memory.images.pop(evict_id, None)

                logger.info(
                    "Stored room reference image for '%s' (id=%s, room_images=%d/%d)",
                    robot_location.name,
                    robot_location.location_id,
                    len(self._room_image_ids),
                    self.max_room_images,
                )
                return True
            except Exception as exc:
                if attempt == 0 and self._recover_chromadb_if_needed(exc):
                    logger.warning(
                        "Retrying tag_location_with_image for '%s' after ChromaDB recovery",
                        robot_location.name,
                    )
                    continue
                logger.exception(
                    "Failed to tag location with image for '%s' (%s)",
                    robot_location.name,
                    type(exc).__name__,
                )
                return False
        return False

    @rpc
    def query_location_by_image(self, image: np.ndarray) -> RobotLocation | None:
        """Recognize the current location by comparing the camera view against
        stored room reference images.

        Uses CLIP image-to-image similarity — no coordinates involved.
        Queries the top-3 closest reference images (a room may have multiple
        reference photos from different angles) and returns the best match.

        Args:
            image: Current camera frame (numpy array, BGR).

        Returns:
            Best-matching RobotLocation, or None if no rooms stored or confidence too low.
            The ``metadata`` dict will contain a ``"distance"`` key (float, lower = better).
        """
        try:
            embedding = self.embedding_provider.get_embedding(image)
            with self._chroma_lock:
                results = self.room_collection.query(
                    query_embeddings=[embedding.tolist()],
                    n_results=3,
                    include=["metadatas", "distances"],
                )
            if not results["ids"] or not results["ids"][0]:
                return None

            # Pick the best match across all reference images (lowest distance)
            best_idx = 0
            best_distance = float("inf")
            for i, dist in enumerate(results["distances"][0]):  # type: ignore[arg-type]
                if dist < best_distance:
                    best_distance = float(dist)
                    best_idx = i

            metadata = results["metadatas"][0][best_idx]  # type: ignore[index]
            location = RobotLocation.from_vector_metadata(metadata)  # type: ignore[arg-type]
            location.metadata["distance"] = best_distance
            logger.info(
                "Visual room match: '%s' (distance=%.4f, top-%d results checked)",
                location.name,
                best_distance,
                len(results["ids"][0]),
            )
            return location
        except Exception as exc:
            if self._recover_chromadb_if_needed(exc):
                logger.warning("ChromaDB recovered after image query error")
            else:
                logger.exception("query_location_by_image failed")
            return None

    @rpc
    def get_room_images(self) -> list[dict[str, object]]:
        """Return stored room reference images, grouped by room name with counts.

        Returns:
            List of dicts with keys: name, count, images (list of {id, timestamp}).
        """
        try:
            data = self.room_collection.get(include=["metadatas"])
            rooms: dict[str, list[dict[str, object]]] = {}
            for meta in data.get("metadatas", []) or []:
                name = meta.get("location_name", "?")
                rooms.setdefault(name, []).append(
                    {
                        "location_id": meta.get("location_id", ""),
                        "timestamp": meta.get("timestamp", 0),
                    }
                )
            result: list[dict[str, object]] = []
            for name, images in sorted(rooms.items()):
                result.append({"name": name, "count": len(images), "images": images})
            return result
        except Exception:
            logger.exception("get_room_images failed")
            return []

    @rpc
    def get_room_image(self, location_id: str) -> np.ndarray | None:
        """Retrieve a raw room reference image by its location_id.

        Args:
            location_id: The location_id from get_room_images() or query_location_by_image().

        Returns:
            Numpy array (BGR) of the stored image, or None if not found.
        """
        if self._visual_memory is None:
            return None
        return self._visual_memory.get(location_id)

    @rpc
    def get_image_by_id(self, frame_id: str) -> np.ndarray | None:
        """Retrieve a stored spatial-memory frame by its frame_id.

        Args:
            frame_id: The frame_id from query_by_text or query_by_text_with_images results.

        Returns:
            Numpy array (BGR) of the stored frame, or None if not found.
        """
        if self._visual_memory is None:
            return None
        return self._visual_memory.get(frame_id)

    @rpc
    def query_by_text_with_images(self, text: str, limit: int = 3) -> list[dict[str, Any]]:
        """Query spatial memory by text, returning results WITH decoded images.

        Like query_by_text but also retrieves the actual pixel data from
        VisualMemory so VLM can re-analyze them. Only top-K results include
        images to keep the payload manageable.

        Args:
            text: Text query to search for.
            limit: Maximum number of results (images attached).

        Returns:
            List of dicts with keys: metadata, id, distance, image (np.ndarray|None).
        """
        try:
            txt_embedding = self.embedding_provider.get_text_embedding(text)
            results = self.vector_db.image_collection.query(
                query_embeddings=[txt_embedding.tolist()],
                n_results=limit,
                include=["metadatas", "distances"],
            )
            if not results or not results["ids"] or not results["ids"][0]:
                return []

            out: list[dict[str, Any]] = []
            for i in range(len(results["ids"][0])):
                vid = results["ids"][0][i]
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                dist = float(results["distances"][0][i]) if results.get("distances") else 1.0
                img = self._visual_memory.get(vid) if self._visual_memory is not None else None
                out.append(
                    {
                        "id": vid,
                        "metadata": meta,
                        "distance": dist,
                        "image": img,
                    }
                )
            return out
        except Exception:
            logger.exception("query_by_text_with_images failed")
            return []


def deploy(  # type: ignore[no-untyped-def]
    dimos: ModuleCoordinator,
    camera: Camera,
):
    spatial_memory = dimos.deploy(SpatialMemory, db_path="/tmp/spatial_memory_db")
    spatial_memory.color_image.connect(camera.color_image)
    spatial_memory.start()
    return spatial_memory
